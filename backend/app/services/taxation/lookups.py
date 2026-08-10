"""Every dropdown the Income Tax workspace needs, in one round trip.

Labels are always the full business term — never a bare abbreviation — because the
same screens are handed to an article clerk and to the business owner, not only to
the chartered accountant who knows the shorthand.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import statutory as stat
from app.models.accounts import Account
from app.models.core import Company
from app.models.tax_credits import TaxChallan, TaxCreditEntry
from app.schemas.taxation import TaxLookupsOut, TaxOptionOut
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.money import money

# Mirrors the preferred-name order in services/taxation/accounts.py so the option
# marked recommended is the account that service would actually resolve.
TAX_PAYABLE_PREFERRED_NAMES = (
    "Income Tax Payable",
    "Provision for Taxation",
    "Provision for Income Tax",
    "Income Tax",
)
TAX_EXPENSE_PREFERRED_NAMES = (
    "Income Tax Expense",
    "Income Tax",
    "Current Tax Expense",
    "Provision for Taxation",
)

CONCESSIONAL_REGIME_CODES = frozenset({"115BAA", "115BAB", "115BAC", "115BA"})

INCOME_HEADS: tuple[tuple[str, str], ...] = (
    ("SALARY", "Salaries"),
    ("HP", "Income from House Property"),
    ("PGBP", "Profits and Gains of Business or Profession"),
    ("CG", "Capital Gains"),
    ("OS", "Income from Other Sources"),
)

FILING_TYPES: tuple[tuple[str, str, str | None], ...] = (
    ("Original", "Original Return", None),
    ("Revised", "Revised Return", "Section 139(5)"),
    ("Belated", "Belated Return", "Section 139(4)"),
    ("Updated", "Updated Return", "Section 139(8A)"),
)

ADJUSTMENT_STAGES: tuple[tuple[str, str], ...] = (
    ("PGBP", "Business Income"),
    ("ICDS", "Income Computation and Disclosure Standards"),
    ("ChapterVIA", "Chapter VI-A Deductions"),
    ("SetOff", "Loss Set-off"),
    ("MAT", "Minimum Alternate Tax"),
    ("Other", "Other"),
)

ADJUSTMENT_DIRECTIONS: tuple[tuple[str, str], ...] = (
    ("Add", "Add back (disallowed for tax)"),
    ("Less", "Deduct (allowed only for tax)"),
)

LOSS_KINDS: tuple[tuple[str, str, str | None], ...] = (
    ("Business", "Business Loss", "Section 72"),
    ("UnabsorbedDep", "Unabsorbed Depreciation", "Section 32(2)"),
    ("Speculation", "Speculation Business Loss", "Section 73"),
    ("STCG", "Short Term Capital Loss", "Section 74"),
    ("LTCG", "Long Term Capital Loss", "Section 74"),
    ("OS", "Loss from Other Sources", "Section 74A"),
)

CHALLAN_TYPES: tuple[tuple[str, str, str | None], ...] = (
    ("AdvanceTax", "Advance Tax", "Section 208"),
    ("SelfAssessment", "Self-Assessment Tax", "Section 140A"),
    ("RegularAssessment", "Tax on Regular Assessment", "Section 156"),
)

MAJOR_HEADS: tuple[tuple[str, str], ...] = (
    ("0020", "Corporation Tax (Companies)"),
    ("0021", "Income Tax (Other than Companies)"),
)

MINOR_HEADS: tuple[tuple[str, str], ...] = (
    ("100", "Advance Tax"),
    ("300", "Self-Assessment Tax"),
    ("400", "Tax on Regular Assessment"),
    ("106", "Tax on Distributed Profits"),
)

CREDIT_KINDS: tuple[tuple[str, str], ...] = (
    ("TDS", "Tax Deducted at Source"),
    ("TCS", "Tax Collected at Source"),
    ("AdvanceTax", "Advance Tax"),
    ("SelfAssessment", "Self-Assessment Tax"),
)

DEDUCTION_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("192", "Section 192 — Tax deducted from salary", "Tax Deducted at Source"),
    ("194", "Section 194 — Dividend paid by a company", "Tax Deducted at Source"),
    ("194A", "Section 194A — Interest other than interest on securities", "Tax Deducted at Source"),
    ("194C", "Section 194C — Payments to contractors", "Tax Deducted at Source"),
    ("194H", "Section 194H — Commission or brokerage", "Tax Deducted at Source"),
    ("194I", "Section 194I — Rent of land, building, plant or machinery", "Tax Deducted at Source"),
    ("194J", "Section 194J — Professional or technical fees", "Tax Deducted at Source"),
    ("194K", "Section 194K — Income from units of a mutual fund", "Tax Deducted at Source"),
    ("194O", "Section 194O — Sales through an electronic commerce operator", "Tax Deducted at Source"),
    ("194Q", "Section 194Q — Purchase of goods above the threshold", "Tax Deducted at Source"),
    ("195", "Section 195 — Payments to a non-resident", "Tax Deducted at Source"),
    ("206C", "Section 206C — Tax collected at source on specified sales", "Tax Collected at Source"),
)

VERIFICATION_MODES: tuple[tuple[str, str], ...] = (
    ("DSC", "Digital Signature Certificate"),
    ("EVC", "Electronic Verification Code"),
)

REGIME_LABELS: dict[str, str] = {
    "Normal": "Normal provisions (no concessional rate elected)",
    "Old": "Normal provisions (old regime)",
    "115BAA": "Concessional rate under Section 115BAA",
    "115BAB": "Concessional rate for new manufacturing companies under Section 115BAB",
    "115BA": "Concessional rate under Section 115BA",
    "115BAC": "Concessional rate under Section 115BAC",
}

REGIME_STATUTORY_REFS: dict[str, str] = {
    "115BAA": "Section 115BAA",
    "115BAB": "Section 115BAB",
    "115BA": "Section 115BA",
    "115BAC": "Section 115BAC",
}


def ay_label(code: str, fy_start_year: int | None = None) -> str:
    """'2025-26' → 'Assessment Year 2025-26 (Financial Year 2024-25)'."""
    if fy_start_year is None:
        try:
            fy_start_year = int(code.split("-")[0]) - 1
        except (ValueError, IndexError):
            return f"Assessment Year {code}"
    return (
        f"Assessment Year {code} "
        f"(Financial Year {fy_start_year}-{(fy_start_year + 1) % 100:02d})"
    )


def regime_label(code: str, title: str | None = None) -> str:
    """Plain-English regime label, preferring the curated wording over pack shorthand."""
    known = REGIME_LABELS.get(code)
    if known:
        return known
    return title or code


def _percent(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(money(value).normalize(), "f")


def _resolve_ay_code(ay_code: str | None, years: list[stat.AssessmentYear]) -> str | None:
    if ay_code:
        return ay_code
    if not years:
        return None
    return max(years, key=lambda y: y.code).code


async def build_lookups(
    db: AsyncSession, *, company_id: uuid.UUID, ay_code: str | None = None
) -> TaxLookupsOut:
    """Assemble every option list for the workspace, company-scoped where tenant data."""
    years = await cat.list_assessment_years(db)
    resolved_ay = _resolve_ay_code(ay_code, years)
    classes = await cat.list_assessee_classes(db)
    regimes = await cat.list_regimes(db)
    characters = await cat.list_income_characters(db)
    provisions = await cat.list_provisions(db)
    blocks = await cat.list_depreciation_blocks(db)
    itr_forms = await cat.list_itr_forms(db, ay_code=resolved_ay) if resolved_ay else []
    deduction_rows = await _deduction_section_rows(db, resolved_ay)

    accounts = await _company_accounts(db, company_id)
    company = await db.get(Company, company_id)
    bank_default = company.default_bank_account_id if company else None

    return TaxLookupsOut(
        ay_code=resolved_ay,
        assessment_years=_assessment_year_options(years),
        entity_classes=_entity_class_options(classes),
        tax_regimes=_regime_options(regimes),
        filing_types=[
            TaxOptionOut(value=value, label=label, statutory_ref=ref)
            for value, label, ref in FILING_TYPES
        ],
        income_heads=[TaxOptionOut(value=value, label=label) for value, label in INCOME_HEADS],
        income_characters=_character_options(characters),
        adjustment_provisions=_provision_options(provisions),
        adjustment_stages=[
            TaxOptionOut(value=value, label=label) for value, label in ADJUSTMENT_STAGES
        ],
        adjustment_directions=[
            TaxOptionOut(value=value, label=label) for value, label in ADJUSTMENT_DIRECTIONS
        ],
        depreciation_blocks=_block_options(blocks),
        loss_kinds=[
            TaxOptionOut(value=value, label=label, statutory_ref=ref)
            for value, label, ref in LOSS_KINDS
        ],
        setoff_groups=_setoff_group_options(characters),
        challan_types=[
            TaxOptionOut(value=value, label=label, statutory_ref=ref)
            for value, label, ref in CHALLAN_TYPES
        ],
        major_heads=[TaxOptionOut(value=value, label=label) for value, label in MAJOR_HEADS],
        minor_heads=[TaxOptionOut(value=value, label=label) for value, label in MINOR_HEADS],
        bank_branch_codes=await _bank_branch_code_options(db, company_id),
        credit_kinds=[TaxOptionOut(value=value, label=label) for value, label in CREDIT_KINDS],
        deduction_sections=_deduction_section_options(deduction_rows),
        deductors=await _deductor_options(db, company_id),
        bank_accounts=_bank_account_options(accounts, bank_default),
        tax_payable_accounts=_account_options_by_root(
            accounts, "Liability", TAX_PAYABLE_PREFERRED_NAMES
        ),
        tax_expense_accounts=_account_options_by_root(
            accounts, "Expense", TAX_EXPENSE_PREFERRED_NAMES
        ),
        itr_forms=[
            TaxOptionOut(
                value=form.form_code,
                label=f"{form.form_code} — {form.title}",
                meta={"schema_version": form.schema_version},
            )
            for form in itr_forms
        ],
        verification_modes=[
            TaxOptionOut(value=value, label=label) for value, label in VERIFICATION_MODES
        ],
    )


def _assessment_year_options(years: list[stat.AssessmentYear]) -> list[TaxOptionOut]:
    return [
        TaxOptionOut(
            value=year.code,
            label=ay_label(year.code, year.fy_start.year),
            meta={
                "fy_start": year.fy_start.isoformat(),
                "fy_end": year.fy_end.isoformat(),
                "prev_ay_code": year.prev_ay_code or "",
            },
        )
        for year in sorted(years, key=lambda y: y.code, reverse=True)
    ]


def _entity_class_options(classes: list[stat.AssesseeClass]) -> list[TaxOptionOut]:
    return [
        TaxOptionOut(
            value=row.code,
            label=row.title,
            meta={
                "default_itr_form": row.default_itr_form or "",
                "pan_4th_chars": row.pan_4th_chars or "",
            },
        )
        for row in classes
    ]


def _regime_options(regimes: list[stat.TaxRegime]) -> list[TaxOptionOut]:
    return [
        TaxOptionOut(
            value=row.code,
            label=regime_label(row.code, row.title),
            group=row.assessee_class_code,
            statutory_ref=REGIME_STATUTORY_REFS.get(row.code),
            meta={
                "assessee_class_code": row.assessee_class_code,
                "is_default": "true" if row.is_default else "false",
                "irrevocable": "true" if row.election_irrevocable else "false",
                "election_form": row.election_form or "",
                "concessional": "true" if row.code in CONCESSIONAL_REGIME_CODES else "false",
            },
        )
        for row in regimes
    ]


def _character_options(characters: list[stat.IncomeCharacter]) -> list[TaxOptionOut]:
    return [
        TaxOptionOut(
            value=row.code,
            label=row.title,
            meta={
                "surcharge_cap_percent": _percent(row.surcharge_cap_percent) or "",
                "setoff_group": row.setoff_group or "",
                "loss_carry_years": str(row.loss_carry_years) if row.loss_carry_years else "",
                "rebate_eligible": "true" if row.rebate_eligible else "false",
            },
        )
        for row in characters
    ]


def _provision_options(provisions: list[stat.Provision]) -> list[TaxOptionOut]:
    stage_labels = dict(ADJUSTMENT_STAGES)
    return [
        TaxOptionOut(
            value=row.section_code,
            label=f"{row.title} (Section {row.section_code})",
            group=stage_labels.get(row.stage, row.stage),
            statutory_ref=row.act_reference,
            meta={
                "stage": row.stage,
                "default_effect": row.default_effect,
                "itr_schedule": row.itr_schedule or "",
            },
        )
        for row in provisions
    ]


def _block_options(blocks: list[stat.DepreciationBlock]) -> list[TaxOptionOut]:
    return [
        TaxOptionOut(
            value=row.block_code,
            label=f"{row.title} — {_percent(row.rate_percent)}%",
            statutory_ref="Section 32",
            meta={
                # ``title`` alone, so a grid that already has a rate column can show the
                # business name without repeating the rate that ``label`` carries.
                "title": row.title,
                "rate_percent": _percent(row.rate_percent) or "0",
                "additional_depreciation_eligible": (
                    "true" if row.additional_depreciation_eligible else "false"
                ),
            },
        )
        for row in blocks
    ]


def _setoff_group_options(characters: list[stat.IncomeCharacter]) -> list[TaxOptionOut]:
    labels = {
        "ORDINARY": "Ordinary Business and Other Income",
        "LTCG": "Long Term Capital Gains",
        "STCG": "Short Term Capital Gains",
        "OS": "Income from Other Sources",
        "SPECIAL": "Specially Taxed Income",
    }
    groups = sorted({row.setoff_group for row in characters if row.setoff_group})
    return [
        TaxOptionOut(value=group, label=labels.get(group, group)) for group in groups
    ]


async def _bank_branch_code_options(
    db: AsyncSession, company_id: uuid.UUID
) -> list[TaxOptionOut]:
    """Bank Branch Codes already used on this company's challans, most used first."""
    rows = (
        await db.execute(
            select(TaxChallan.bsr_code, func.count(TaxChallan.id))
            .where(TaxChallan.company_id == company_id)
            .group_by(TaxChallan.bsr_code)
            .order_by(func.count(TaxChallan.id).desc(), TaxChallan.bsr_code)
        )
    ).all()
    return [
        TaxOptionOut(
            value=code,
            label=f"{code} — used on {int(used)} earlier challans",
            hint="Bank Branch Code printed on the challan counterfoil",
            meta={"challan_count": str(int(used))},
        )
        for code, used in rows
        if code
    ]


async def _deductor_options(db: AsyncSession, company_id: uuid.UUID) -> list[TaxOptionOut]:
    rows = (
        await db.execute(
            select(TaxCreditEntry.deductor_tan, TaxCreditEntry.deductor_name)
            .where(
                TaxCreditEntry.company_id == company_id,
                TaxCreditEntry.deductor_tan.is_not(None),
            )
            .group_by(TaxCreditEntry.deductor_tan, TaxCreditEntry.deductor_name)
            .order_by(TaxCreditEntry.deductor_name, TaxCreditEntry.deductor_tan)
        )
    ).all()
    out: list[TaxOptionOut] = []
    seen: set[str] = set()
    for tan, name in rows:
        if not tan or tan in seen:
            continue
        seen.add(tan)
        out.append(
            TaxOptionOut(
                value=tan,
                label=f"{name} ({tan})" if name else f"Deductor with Tax Deduction Account Number {tan}",
                hint="Tax Deduction Account Number of the deductor",
                meta={"deductor_name": name or ""},
            )
        )
    return out


async def _deduction_section_rows(
    db: AsyncSession, ay_code: str | None
) -> list[stat.DeductionSection]:
    if not ay_code:
        return []
    fav = await cat.get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        return []
    result = await db.scalars(
        select(stat.DeductionSection)
        .where(stat.DeductionSection.finance_act_version_id == fav.id)
        .order_by(stat.DeductionSection.section_code)
    )
    return list(result.all())


def _deduction_section_options(rows: list[stat.DeductionSection]) -> list[TaxOptionOut]:
    """Common deduction/collection sections, merged with the catalogue's own sections."""
    options = [
        TaxOptionOut(
            value=code,
            label=label,
            group=group,
            statutory_ref=f"Section {code}",
        )
        for code, label, group in DEDUCTION_SECTIONS
    ]
    known = {opt.value for opt in options}
    for row in rows:
        if row.section_code in known:
            continue
        options.append(
            TaxOptionOut(
                value=row.section_code,
                label=f"Section {row.section_code} — {row.title}",
                group="Chapter VI-A Deductions",
                statutory_ref=f"Section {row.section_code}",
            )
        )
    return options


async def _company_accounts(db: AsyncSession, company_id: uuid.UUID) -> list[Account]:
    result = await db.scalars(
        select(Account)
        .where(
            Account.company_id == company_id,
            Account.is_group.is_(False),
            Account.disabled.is_(False),
        )
        .order_by(Account.account_name)
    )
    return list(result.all())


def _account_option(account: Account, *, recommended: bool) -> TaxOptionOut:
    meta = {"account_type": account.account_type or "", "root_type": account.root_type}
    if recommended:
        meta["recommended"] = "true"
    return TaxOptionOut(
        value=str(account.id),
        label=account.account_name,
        group=account.account_type or account.root_type,
        meta=meta,
    )


def _bank_account_options(
    accounts: list[Account], default_bank_account_id: uuid.UUID | None
) -> list[TaxOptionOut]:
    rows = [a for a in accounts if (a.account_type or "") in ("Bank", "Cash")]
    ordered = sorted(rows, key=lambda a: (a.id != default_bank_account_id, a.account_name))
    return [
        _account_option(a, recommended=(a.id == default_bank_account_id and i == 0))
        for i, a in enumerate(ordered)
    ]


def _account_options_by_root(
    accounts: list[Account], root_type: str, preferred_names: tuple[str, ...]
) -> list[TaxOptionOut]:
    rows = [a for a in accounts if a.root_type == root_type]
    rank = {name: i for i, name in enumerate(preferred_names)}
    ordered = sorted(rows, key=lambda a: (rank.get(a.account_name, len(rank)), a.account_name))
    return [
        _account_option(a, recommended=(i == 0 and a.account_name in rank))
        for i, a in enumerate(ordered)
    ]
