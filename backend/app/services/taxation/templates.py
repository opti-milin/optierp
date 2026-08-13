"""Starting presets for a new computation, plus carry-forward from the previous year.

Templates are **system** presets: they describe law-adjacent starting points (entity
class, regime, whether Minimum Alternate Tax applies, the disallowances a reviewer
always looks for), not tenant data. Per the machine-first rule they therefore get
neither a table nor a descriptor — they live here as frozen dataclasses and no
migration is needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models import statutory as stat
from app.models.base import DOCSTATUS_CANCELLED
from app.models.tax_computation import TaxComputation
from app.models.tax_corporate import TaxDepreciationRegister
from app.schemas.taxation import (
    TaxComputationAdjustmentLineIn,
    TaxComputationCreate,
    TaxComputationIncomeLineIn,
    TaxComputationUpdate,
    TaxCopyPreviousYearIn,
    TaxCopyPreviousYearOut,
    TaxPopulateFromBooksIn,
    TaxTemplateAdjustmentOut,
    TaxTemplateApplyIn,
    TaxTemplateOut,
)
from app.services.taxation import books as books_service
from app.services.taxation import computations as computation_service
from app.services.taxation import depreciation as dep_service
from app.services.taxation import loss_setoff as loss_service
from app.services.taxation import registration as registration_service
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.lookups import CONCESSIONAL_REGIME_CODES, REGIME_STATUTORY_REFS

MAT_NOT_APPLICABLE_REGIMES = frozenset({"115BAA", "115BAB"})


@dataclass(frozen=True, slots=True)
class TaxTemplateAdjustment:
    section_code: str
    label: str
    stage: str = "PGBP"
    direction: str = "Add"
    hint: str | None = None


@dataclass(frozen=True, slots=True)
class TaxTemplate:
    """One system preset. Nothing here is tenant-editable."""

    code: str
    title: str
    description: str
    entity_class_code: str
    regime_code: str
    audit_applicable: bool = True
    mat_applicable: bool = True
    requires_book_profit: bool = False
    forfeited_incentives: tuple[str, ...] = ()
    income_heads: tuple[str, ...] = ("PGBP",)
    suggested_adjustments: tuple[TaxTemplateAdjustment, ...] = ()
    notes: tuple[str, ...] = ()


_COMMON_DISALLOWANCES: tuple[TaxTemplateAdjustment, ...] = (
    TaxTemplateAdjustment(
        section_code="37-personal",
        label="Personal, capital or non-business expenditure disallowed",
        hint="Expenditure not laid out wholly and exclusively for the business.",
    ),
    TaxTemplateAdjustment(
        section_code="40(a)(ia)",
        label="Expenditure on which tax was not deducted at source",
        hint="Thirty per cent of the payment is disallowed where tax was not deducted or paid.",
    ),
    TaxTemplateAdjustment(
        section_code="43B",
        label="Statutory dues unpaid by the return due date",
        hint="Taxes, duties and contributions still unpaid when the return falls due.",
    ),
    TaxTemplateAdjustment(
        section_code="36(1)(va)",
        label="Employee contributions not deposited by the due date",
        hint="Provident fund and similar employee contributions deposited late.",
    ),
    TaxTemplateAdjustment(
        section_code="14A",
        label="Expenditure incurred to earn exempt income",
        hint="Expenditure attributable to income that does not form part of total income.",
    ),
)

_DEPRECIATION_DEDUCTION = TaxTemplateAdjustment(
    section_code="32",
    label="Depreciation allowable under the Income-tax Act",
    direction="Less",
    hint="Claimed from the block-wise written-down value register, not from the books.",
)

PVT_LTD_NORMAL = TaxTemplate(
    code="PVT_LTD_NORMAL",
    title="Private Limited Company — normal provisions",
    description=(
        "A domestic company taxed under the normal provisions, keeping every incentive and "
        "deduction, and compared against Minimum Alternate Tax on book profit."
    ),
    entity_class_code="Company",
    regime_code="Normal",
    mat_applicable=True,
    requires_book_profit=True,
    income_heads=("PGBP", "OS"),
    suggested_adjustments=(*_COMMON_DISALLOWANCES, _DEPRECIATION_DEDUCTION),
    notes=(
        "Minimum Alternate Tax under Section 115JB applies, so the book profit is needed.",
        "Additional depreciation under Section 32(1)(iia) and Chapter VI-A deductions remain available.",
    ),
)

COMPANY_115BAA = TaxTemplate(
    code="COMPANY_115BAA",
    title="Company opting for the concessional rate under Section 115BAA",
    description=(
        "A domestic company electing the twenty-two per cent concessional rate. The election is "
        "irrevocable and gives up most incentives, but Minimum Alternate Tax no longer applies."
    ),
    entity_class_code="Company",
    regime_code="115BAA",
    mat_applicable=False,
    requires_book_profit=False,
    forfeited_incentives=(
        "Additional depreciation under Section 32(1)(iia)",
        "Most Chapter VI-A deductions other than Section 80JJAA and Section 80M",
        "Set-off of brought forward loss attributable to those deductions",
        "Minimum Alternate Tax under Section 115JB does not apply",
    ),
    income_heads=("PGBP", "OS"),
    suggested_adjustments=(*_COMMON_DISALLOWANCES, _DEPRECIATION_DEDUCTION),
    notes=(
        "File Form 10-IC on or before the return due date; the election cannot be withdrawn.",
        "Brought forward Minimum Alternate Tax credit lapses on electing this regime.",
    ),
)

COMPANY_115BAB = TaxTemplate(
    code="COMPANY_115BAB",
    title="New manufacturing company under Section 115BAB",
    description=(
        "A new domestic manufacturing company electing the fifteen per cent concessional rate. "
        "The election is irrevocable and subject to the manufacturing conditions of the section."
    ),
    entity_class_code="Company",
    regime_code="115BAB",
    mat_applicable=False,
    requires_book_profit=False,
    forfeited_incentives=(
        "Additional depreciation under Section 32(1)(iia)",
        "Most Chapter VI-A deductions other than Section 80JJAA and Section 80M",
        "Set-off of brought forward loss attributable to those deductions",
        "Minimum Alternate Tax under Section 115JB does not apply",
    ),
    income_heads=("PGBP",),
    suggested_adjustments=(*_COMMON_DISALLOWANCES, _DEPRECIATION_DEDUCTION),
    notes=(
        "File Form 10-ID on or before the return due date for the first year.",
        "Income other than from manufacturing is charged at the rate specified in Section 115BAB.",
    ),
)

SMALL_COMPANY = TaxTemplate(
    code="SMALL_COMPANY",
    title="Small company — turnover-based rate under normal provisions",
    description=(
        "A small or micro company whose turnover is within the prescribed limit, taxed at the "
        "lower twenty-five per cent rate under the normal provisions."
    ),
    entity_class_code="Company",
    regime_code="Normal",
    mat_applicable=True,
    requires_book_profit=True,
    income_heads=("PGBP",),
    suggested_adjustments=(*_COMMON_DISALLOWANCES, _DEPRECIATION_DEDUCTION),
    notes=(
        "The lower rate depends on the turnover of the prescribed earlier year — confirm it.",
        "Minimum Alternate Tax under Section 115JB still applies, so the book profit is needed.",
        "Interest on payments to micro and small enterprises is disallowed under Section 43B(h).",
    ),
)

TEMPLATES: tuple[TaxTemplate, ...] = (
    PVT_LTD_NORMAL,
    COMPANY_115BAA,
    COMPANY_115BAB,
    SMALL_COMPANY,
)
TEMPLATES_BY_CODE: dict[str, TaxTemplate] = {t.code: t for t in TEMPLATES}


def get_template(code: str) -> TaxTemplate:
    template = TEMPLATES_BY_CODE.get(code)
    if template is None:
        raise ValidationError(
            f"Unknown template '{code}'", code="unknown_template", field="template_code"
        )
    return template


def template_out(
    template: TaxTemplate,
    *,
    available: bool = True,
    unavailable_reason: str | None = None,
    recommended: bool = False,
) -> TaxTemplateOut:
    return TaxTemplateOut(
        code=template.code,
        title=template.title,
        description=template.description,
        entity_class_code=template.entity_class_code,
        regime_code=template.regime_code,
        regime_statutory_ref=REGIME_STATUTORY_REFS.get(template.regime_code),
        audit_applicable=template.audit_applicable,
        mat_applicable=template.mat_applicable,
        requires_book_profit=template.requires_book_profit,
        forfeited_incentives=list(template.forfeited_incentives),
        income_heads=list(template.income_heads),
        suggested_adjustments=[
            TaxTemplateAdjustmentOut(
                section_code=adj.section_code,
                label=adj.label,
                stage=adj.stage,
                direction=adj.direction,
                hint=adj.hint,
            )
            for adj in template.suggested_adjustments
        ],
        notes=list(template.notes),
        available=available,
        unavailable_reason=unavailable_reason,
        recommended=recommended,
    )


def resolve_availability(
    template: TaxTemplate,
    *,
    regime_pairs: set[tuple[str, str]],
    scheduled_pairs: set[tuple[str, str]],
    ay_code: str | None,
) -> tuple[bool, str | None]:
    """A template is usable only if its regime and its rates exist for that year."""
    pair = (template.entity_class_code, template.regime_code)
    year = ay_code or "this assessment year"
    if pair not in regime_pairs:
        return False, (
            f"The {template.regime_code} regime is not available for "
            f"{template.entity_class_code} in the statutory catalogue."
        )
    if scheduled_pairs and pair not in scheduled_pairs:
        return False, (
            f"No rates are published for {template.entity_class_code} under "
            f"{template.regime_code} for assessment year {year}."
        )
    return True, None


async def list_templates(
    db: AsyncSession, *, company_id: uuid.UUID, ay_code: str | None = None
) -> list[TaxTemplateOut]:
    """Presets with per-year availability and one recommendation for this company."""
    regimes = await cat.list_regimes(db)
    regime_pairs = {(r.assessee_class_code, r.code) for r in regimes}
    default_regimes = {r.assessee_class_code: r.code for r in regimes if r.is_default}

    scheduled_pairs: set[tuple[str, str]] = set()
    if ay_code:
        schedules = await cat.list_rate_schedules(db, ay_code=ay_code)
        scheduled_pairs = {(s.assessee_class_code, s.regime_code) for s in schedules}

    reg = await registration_service.get_registration(db, company_id)
    election = (
        await registration_service.get_election(db, company_id, ay_code) if ay_code else None
    )
    entity_class = reg.assessee_class_code if reg else None
    elected_regime = election.regime_code if election else None

    out: list[TaxTemplateOut] = []
    recommended_taken = False
    for template in TEMPLATES:
        available, reason = resolve_availability(
            template,
            regime_pairs=regime_pairs,
            scheduled_pairs=scheduled_pairs,
            ay_code=ay_code,
        )
        recommended = False
        if available and not recommended_taken and entity_class == template.entity_class_code:
            wanted = elected_regime or default_regimes.get(template.entity_class_code)
            if wanted == template.regime_code:
                recommended = True
                recommended_taken = True
        out.append(
            template_out(
                template,
                available=available,
                unavailable_reason=reason,
                recommended=recommended,
            )
        )
    return out


def _template_income_lines(template: TaxTemplate) -> list[TaxComputationIncomeLineIn]:
    return [
        TaxComputationIncomeLineIn(
            seq=i,
            head=head,
            income_character_code="ORDINARY",
            gross=ZERO,
            deductions=ZERO,
            net=ZERO,
        )
        for i, head in enumerate(template.income_heads)
    ]


def _template_adjustment_lines(template: TaxTemplate) -> list[TaxComputationAdjustmentLineIn]:
    return [
        TaxComputationAdjustmentLineIn(
            provision_section_code=adj.section_code,
            section_code=adj.section_code,
            stage=adj.stage,
            direction=adj.direction,
            description=adj.label,
            amount=ZERO,
            status="Manual",
        )
        for adj in template.suggested_adjustments
    ]


async def apply_template(
    db: AsyncSession, payload: TaxTemplateApplyIn, user: CurrentUser
) -> TaxComputation:
    """Create a pre-populated computation from a preset, optionally carrying data in."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    template = get_template(payload.template_code)

    regimes = await cat.list_regimes(db)
    schedules = await cat.list_rate_schedules(db, ay_code=payload.ay_code)
    available, reason = resolve_availability(
        template,
        regime_pairs={(r.assessee_class_code, r.code) for r in regimes},
        scheduled_pairs={(s.assessee_class_code, s.regime_code) for s in schedules},
        ay_code=payload.ay_code,
    )
    if not available:
        raise ValidationError(
            reason or "This template is not available for the selected assessment year",
            code="template_regime_unavailable",
            field="template_code",
        )

    book_profit: Decimal | None = None
    if template.mat_applicable and payload.book_profit_115jb is not None:
        book_profit = q(money(payload.book_profit_115jb))

    doc = await computation_service.create_computation(
        db,
        TaxComputationCreate(
            ay_code=payload.ay_code,
            assessee_class_code=template.entity_class_code,
            regime_code=template.regime_code,
            filing_type=payload.filing_type,
            audit_applicable=template.audit_applicable,
            remarks=payload.remarks or template.title,
            book_profit_115jb=book_profit,
            income_lines=_template_income_lines(template),
            adjustment_lines=_template_adjustment_lines(template),
        ),
        user,
    )

    if payload.copy_from_previous_year:
        await copy_previous_year(
            db,
            computation_id=doc.id,
            payload=TaxCopyPreviousYearIn(overwrite_existing=True),
            user=user,
        )
    if payload.populate_from_books:
        await books_service.populate_from_books(
            db,
            computation_id=doc.id,
            payload=TaxPopulateFromBooksIn(set_book_profit=template.mat_applicable),
            user=user,
        )
    return await computation_service.get_computation(db, user.company_id, doc.id)


# --- Carry forward from the previous assessment year ---------------------------------


@dataclass(slots=True)
class _CopyTally:
    income_lines: int = 0
    adjustment_lines: int = 0
    depreciation_blocks: int = 0
    notes: list[str] = field(default_factory=list)


async def _latest_computation_for_ay(
    db: AsyncSession, *, company_id: uuid.UUID, ay_code: str
) -> TaxComputation | None:
    return await db.scalar(
        select(TaxComputation)
        .where(
            TaxComputation.company_id == company_id,
            TaxComputation.ay_code == ay_code,
            TaxComputation.docstatus != DOCSTATUS_CANCELLED,
        )
        .options(
            selectinload(TaxComputation.income_lines),
            selectinload(TaxComputation.adjustment_lines),
        )
        .order_by(TaxComputation.creation.desc())
        .limit(1)
    )


def zeroed_income_structure(source: TaxComputation) -> list[TaxComputationIncomeLineIn]:
    """Head, character and description carried forward; amounts start at nil."""
    return [
        TaxComputationIncomeLineIn(
            seq=line.seq,
            head=line.head,
            income_character_code=line.income_character_code,
            sub_ref=line.sub_ref,
            gross=ZERO,
            deductions=ZERO,
            net=ZERO,
        )
        for line in sorted(source.income_lines, key=lambda ln: ln.seq)
    ]


def zeroed_standing_adjustments(source: TaxComputation) -> list[TaxComputationAdjustmentLineIn]:
    """Manual (standing) adjustments carried forward with nil amounts to be re-entered."""
    return [
        TaxComputationAdjustmentLineIn(
            provision_section_code=line.provision_section_code,
            rule_code=line.rule_code,
            section_code=line.section_code,
            stage=line.stage,
            description=line.description,
            direction=line.direction,
            amount=ZERO,
            status="Manual",
        )
        for line in source.adjustment_lines
        if line.run_id is None and line.status == "Manual"
    ]


async def copy_previous_year(
    db: AsyncSession,
    *,
    computation_id: uuid.UUID,
    payload: TaxCopyPreviousYearIn,
    user: CurrentUser,
) -> TaxCopyPreviousYearOut:
    """Carry the previous year's structure, opening written-down values and standing entries."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    company_id = user.company_id
    target = await computation_service.get_computation(db, company_id, computation_id)
    if target.docstatus != 0:
        raise ValidationError(
            "Figures can only be carried forward while the computation is a draft",
            code="not_draft",
            field="docstatus",
        )

    tally = _CopyTally()
    source_ay = payload.source_ay_code
    if not source_ay:
        ay_row = await db.scalar(
            select(stat.AssessmentYear).where(stat.AssessmentYear.code == target.ay_code)
        )
        source_ay = ay_row.prev_ay_code if ay_row else None
    if not source_ay:
        tally.notes.append(
            f"Assessment year {target.ay_code} has no previous year in the statutory "
            "catalogue, so there was nothing to carry forward."
        )
        return await _copy_result(db, company_id, target, None, None, tally)

    source = await _latest_computation_for_ay(db, company_id=company_id, ay_code=source_ay)
    if source is None:
        tally.notes.append(
            f"No computation exists for assessment year {source_ay}, so there was nothing "
            "to carry forward. Enter this year's figures directly."
        )
        return await _copy_result(db, company_id, target, source_ay, None, tally)

    income_lines: list[TaxComputationIncomeLineIn] | None = None
    if payload.copy_income_heads:
        if target.income_lines and not payload.overwrite_existing:
            tally.notes.append(
                "This year already has income lines, so the previous year's heads were left alone."
            )
        else:
            carried_income = zeroed_income_structure(source)
            if carried_income:
                income_lines = carried_income
                tally.income_lines = len(carried_income)
                tally.notes.append(
                    f"{tally.income_lines} income head(s) carried forward from {source.name} "
                    "with nil amounts — enter this year's figures."
                )
            else:
                tally.notes.append(
                    f"{source.name} has no income heads, so none were carried forward."
                )

    adjustment_lines: list[TaxComputationAdjustmentLineIn] | None = None
    if payload.copy_adjustments:
        draft_existing = [ln for ln in target.adjustment_lines if ln.run_id is None]
        if draft_existing and not payload.overwrite_existing:
            tally.notes.append(
                "This year already has adjustment lines, so the previous year's standing "
                "adjustments were left alone."
            )
        else:
            carried_adjustments = zeroed_standing_adjustments(source)
            if carried_adjustments:
                adjustment_lines = carried_adjustments
                tally.adjustment_lines = len(carried_adjustments)
                tally.notes.append(
                    f"{tally.adjustment_lines} standing adjustment(s) carried forward with nil "
                    "amounts — only the figures need re-entering."
                )
            else:
                tally.notes.append(
                    f"{source.name} has no standing adjustments, so none were carried forward."
                )

    if income_lines is not None or adjustment_lines is not None:
        await computation_service.update_computation(
            db,
            computation_id,
            TaxComputationUpdate(
                income_lines=income_lines,
                adjustment_lines=adjustment_lines,
            ),
            user,
        )

    if payload.carry_depreciation_blocks:
        tally.depreciation_blocks = await _carry_depreciation_blocks(
            db,
            company_id=company_id,
            source_ay=source_ay,
            target_ay=target.ay_code,
            overwrite_existing=payload.overwrite_existing,
            user=user,
            notes=tally.notes,
        )

    return await _copy_result(db, company_id, target, source_ay, source.id, tally)


async def _carry_depreciation_blocks(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    source_ay: str,
    target_ay: str,
    overwrite_existing: bool,
    user: CurrentUser,
    notes: list[str],
) -> int:
    """Opening written-down value of each block becomes last year's closing value."""
    prior = await dep_service.list_registers(db, company_id, ay_code=source_ay)
    if not prior:
        notes.append(
            f"No depreciation register exists for assessment year {source_ay}, so no opening "
            "written-down values were carried forward."
        )
        return 0
    current = await dep_service.list_registers(db, company_id, ay_code=target_ay)
    by_block = {row.block_code: row for row in current}
    blocks = await cat.list_depreciation_blocks(db)
    rates = {row.block_code: row for row in blocks}

    carried = 0
    for row in prior:
        opening = q(money(row.closing_wdv))
        block = rates.get(row.block_code)
        rate = money(block.rate_percent) if block else money(row.rate_percent)
        existing = by_block.get(row.block_code)
        if existing is not None and not overwrite_existing:
            continue
        depreciation, additional, closing = dep_service.compute_block_depreciation(
            opening_wdv=opening,
            additions_full=ZERO,
            additions_half=ZERO,
            deletions=ZERO,
            rate_percent=rate,
            additional_eligible=False,
        )
        if existing is not None:
            existing.opening_wdv = opening
            existing.rate_percent = rate
            existing.depreciation_amount = depreciation
            existing.additional_depreciation_amount = additional
            existing.closing_wdv = closing
            existing.modified_by = user.id
        else:
            db.add(
                TaxDepreciationRegister(
                    company_id=company_id,
                    ay_code=target_ay,
                    block_code=row.block_code,
                    rate_percent=rate,
                    opening_wdv=opening,
                    additions_full=ZERO,
                    additions_half=ZERO,
                    deletions=ZERO,
                    depreciation_amount=depreciation,
                    additional_depreciation_amount=additional,
                    closing_wdv=closing,
                    remarks=f"Opening written-down value carried from {source_ay}",
                    owner=user.id,
                    modified_by=user.id,
                )
            )
        carried += 1
    if carried:
        await db.commit()
        notes.append(
            f"{carried} depreciation block(s) opened with the closing written-down value of "
            f"{source_ay}; add this year's additions and deletions."
        )
    else:
        notes.append(
            "Depreciation blocks already exist for this year, so opening written-down values "
            "were left alone."
        )
    return carried


async def _copy_result(
    db: AsyncSession,
    company_id: uuid.UUID,
    target: TaxComputation,
    source_ay: str | None,
    source_id: uuid.UUID | None,
    tally: _CopyTally,
) -> TaxCopyPreviousYearOut:
    losses = await loss_service.available_bf_losses(db, company_id, target.ay_code)
    if losses:
        tally.notes.append(
            f"{len(losses)} brought forward loss entr(ies) are available for set-off this year."
        )
    refreshed = await computation_service.get_computation(db, company_id, target.id)
    return TaxCopyPreviousYearOut(
        source_ay_code=source_ay,
        source_computation_id=source_id,
        income_lines_copied=tally.income_lines,
        adjustment_lines_copied=tally.adjustment_lines,
        depreciation_blocks_carried=tally.depreciation_blocks,
        brought_forward_losses_available=len(losses),
        notes=tally.notes,
        computation=computation_service.computation_out(refreshed),
    )


def mat_applies(entity_class_code: str, regime_code: str) -> bool:
    """Section 115JB applies to companies except those electing 115BAA or 115BAB."""
    return entity_class_code == "Company" and regime_code not in MAT_NOT_APPLICABLE_REGIMES


def is_concessional(regime_code: str) -> bool:
    return regime_code in CONCESSIONAL_REGIME_CODES
