"""Statement of Total Income and Tax Computation — the format a reviewer reads.

Built from the same ``WorkspaceFacts`` snapshot the validator uses plus a
non-persisting preview, so the statement always reflects the worksheet as it
stands rather than whatever run happened to be saved last.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import DOCSTATUS_CANCELLED
from app.models.tax_computation import TaxComputation, TaxComputationResult
from app.schemas.taxation import (
    TaxPreviewOut,
    TaxStatementOut,
    TaxStatementRowOut,
    TaxStatementSectionOut,
)
from app.services.taxation import books as books_service
from app.services.taxation import preview as preview_service
from app.services.taxation import validation as validation_service
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.validation import WorkspaceFacts

HEAD_LABELS: dict[str, str] = {
    "SALARY": "Salaries",
    "HP": "Income from House Property",
    "PGBP": "Profits and Gains of Business or Profession",
    "CG": "Capital Gains",
    "OS": "Income from Other Sources",
}

CHARACTER_LABELS: dict[str, str] = {
    "ORDINARY": "Ordinary Business Income",
    "LTCG_112A": "Long Term Capital Gains (Section 112A)",
    "LTCG_112": "Long Term Capital Gains (Section 112)",
    "STCG_111A": "Short Term Capital Gains (Section 111A)",
    "LOTTERY_115BB": "Winnings from Lotteries and Games (Section 115BB)",
    "VDA_115BBH": "Income from Virtual Digital Assets (Section 115BBH)",
    "DIVIDEND": "Dividend Income",
}

ENTRY_KIND_LABELS: dict[str, str] = {
    "Created": "Credit created",
    "Utilised": "Credit set off",
    "Expired": "Credit expired",
}

_DEDUCT_DIRECTIONS = frozenset({"deduct", "less", "deduction"})


def head_label(code: str | None) -> str:
    key = (code or "PGBP").upper()
    return HEAD_LABELS.get(key, key.replace("_", " ").title())


def character_label(code: str | None) -> str:
    key = (code or "ORDINARY").upper()
    return CHARACTER_LABELS.get(key, key.replace("_", " ").title())


def _row(
    key: str,
    label: str,
    amount: Decimal | None = None,
    *,
    statutory_ref: str | None = None,
    indent: int = 0,
    emphasis: str = "normal",
    note: str | None = None,
) -> TaxStatementRowOut:
    return TaxStatementRowOut(
        key=key,
        label=label,
        amount=amount,
        statutory_ref=statutory_ref,
        indent=indent,
        emphasis=emphasis,
        note=note,
    )


def _is_deduction(direction: str | None) -> bool:
    return (direction or "Add").strip().lower() in _DEDUCT_DIRECTIONS


def build_income_section(facts: WorkspaceFacts) -> TaxStatementSectionOut:
    """Head-wise income with a Gross Total Income subtotal."""
    rows: list[TaxStatementRowOut] = []
    gross_total = ZERO
    by_head: dict[str, list[TaxStatementRowOut]] = {}
    head_totals: dict[str, Decimal] = {}

    for line in facts.income_lines:
        head = str(line.head or "PGBP")
        net = q(money(line.net))
        head_totals[head] = q(head_totals.get(head, ZERO) + net)
        detail = line.sub_ref or character_label(line.income_character_code)
        by_head.setdefault(head, []).append(
            _row(f"income.{head}.{line.seq}", detail, net, indent=2)
        )
        gross_total = q(gross_total + net)

    for head, lines in by_head.items():
        if len(lines) > 1:
            # Group header only — detail lines carry the working amounts; the head
            # total would otherwise sit in the same column and break the statement.
            rows.append(_row(f"income.head.{head}", head_label(head), None, indent=1))
            rows.extend(lines)
        else:
            rows.append(
                _row(f"income.head.{head}", head_label(head), head_totals[head], indent=1)
            )

    if not rows:
        rows.append(
            _row("income.empty", "No income has been entered for this year yet.", None, indent=1)
        )

    rows.append(
        _row(
            "income.gross_total",
            "Gross Total Income",
            gross_total,
            emphasis="subtotal",
        )
    )
    return TaxStatementSectionOut(
        key="income", title="Computation of Total Income", rows=rows
    )


def _section_reference(section_code: str) -> str | None:
    """Turn an adjustment's catalogue code into the section a CA would cite.

    Catalogue codes qualify the provision so two rules under one section stay distinct
    (``37-CSR`` and ``37-penalty``). Only the statutory part belongs on the statement —
    the qualifier is an internal key and must not reach the page.
    """
    code = section_code.strip()
    if not code:
        return None
    return f"Section {code.split('-', 1)[0]}"


def build_adjustments_section(facts: WorkspaceFacts) -> TaxStatementSectionOut:
    """Add-backs then deductions, each labelled with its provision."""
    rows: list[TaxStatementRowOut] = []
    add_rows: list[TaxStatementRowOut] = []
    less_rows: list[TaxStatementRowOut] = []
    add_total = ZERO
    less_total = ZERO

    for line in facts.adjustment_lines:
        if line.run_id is not None:
            continue
        amount = q(money(line.override_amount if line.override_amount is not None else line.amount))
        if amount == ZERO:
            continue
        section_code = (line.section_code or "").strip()
        label = line.description or "Adjustment"
        ref = _section_reference(section_code)
        target = less_rows if _is_deduction(line.direction) else add_rows
        target.append(_row(f"adj.{line.id}", label, amount, statutory_ref=ref, indent=1))
        if _is_deduction(line.direction):
            less_total = q(less_total + amount)
        else:
            add_total = q(add_total + amount)

    if add_rows:
        rows.append(_row("adj.add.head", "Amounts disallowed and added back", None))
        rows.extend(add_rows)
        rows.append(_row("adj.add.total", "Total added back", add_total, emphasis="subtotal"))
    if less_rows:
        rows.append(_row("adj.less.head", "Amounts allowed only for tax purposes", None))
        rows.extend(less_rows)
        rows.append(_row("adj.less.total", "Total deducted", less_total, emphasis="subtotal"))
    if not rows:
        rows.append(_row("adj.empty", "No adjustments have been recorded.", None, indent=1))
    else:
        rows.append(
            _row(
                "adj.net",
                "Net effect of adjustments on business income",
                q(add_total - less_total),
                emphasis="subtotal",
            )
        )
    return TaxStatementSectionOut(
        key="adjustments", title="Adjustments to Business Income", rows=rows
    )


def build_depreciation_section(facts: WorkspaceFacts) -> TaxStatementSectionOut:
    """Block-wise depreciation allowable under the Income-tax Act."""
    rows: list[TaxStatementRowOut] = []
    total = ZERO
    for register in facts.registers:
        allowable = q(
            money(register.depreciation_amount) + money(register.additional_depreciation_amount)
        )
        total = q(total + allowable)
        rows.append(
            _row(
                f"dep.{register.block_code}",
                f"{register.block_code} at {q(money(register.rate_percent))}%",
                allowable,
                indent=1,
                note=(
                    f"Opening written down value {q(money(register.opening_wdv))}, "
                    f"closing written down value {q(money(register.closing_wdv))}"
                ),
            )
        )
    if not rows:
        rows.append(
            _row(
                "dep.empty",
                "No depreciation register has been prepared for this year.",
                None,
                indent=1,
            )
        )
    rows.append(
        _row(
            "dep.total",
            "Total depreciation claimed under the Income-tax Act",
            total,
            statutory_ref="Section 32",
            emphasis="subtotal",
        )
    )
    return TaxStatementSectionOut(
        key="depreciation", title="Depreciation under the Income-tax Act", rows=rows
    )


def build_setoff_section(facts: WorkspaceFacts) -> TaxStatementSectionOut:
    rows: list[TaxStatementRowOut] = []
    total = ZERO
    for entry in sorted(facts.setoff_entries, key=lambda e: e.sequence):
        amount = q(money(entry.amount_set_off))
        total = q(total + amount)
        rows.append(
            _row(
                f"setoff.{entry.id}",
                f"Set off against {character_label(entry.against_character)}",
                amount,
                indent=1,
            )
        )
    if not rows:
        rows.append(
            _row(
                "setoff.empty",
                "No brought forward loss was set off against this year's income.",
                None,
                indent=1,
            )
        )
    rows.append(
        _row(
            "setoff.total",
            "Total brought forward losses set off",
            total,
            statutory_ref="Sections 70 to 74",
            emphasis="subtotal",
        )
    )
    return TaxStatementSectionOut(
        key="setoff", title="Set-off of Brought Forward Losses", rows=rows
    )


def build_total_income_section(preview: TaxPreviewOut) -> TaxStatementSectionOut:
    rows = [
        _row("ti.gross", "Gross Total Income", preview.gross_total_income),
        _row(
            "ti.depreciation",
            "Less: Depreciation under the Income-tax Act",
            preview.tax_depreciation_claimed,
            statutory_ref="Section 32",
            indent=1,
        ),
        _row(
            "ti.setoff",
            "Less: Brought Forward Losses Set Off",
            preview.losses_set_off,
            statutory_ref="Sections 70 to 74",
            indent=1,
        ),
        _row(
            "ti.total",
            "Total Income (rounded to the nearest ten rupees)",
            preview.total_income,
            statutory_ref="Section 288A",
            emphasis="total",
        ),
    ]
    return TaxStatementSectionOut(key="total_income", title="Total Income", rows=rows)


def build_tax_section(preview: TaxPreviewOut) -> TaxStatementSectionOut:
    rows = [
        _row("tax.on_total_income", "Tax on Total Income", preview.tax_on_total_income),
        _row(
            "tax.rebate",
            "Less: Rebate",
            preview.rebate_amount,
            statutory_ref="Section 87A",
            indent=1,
        ),
        _row("tax.surcharge_gross", "Add: Surcharge", preview.surcharge_before_relief, indent=1),
        _row("tax.marginal_relief", "Less: Marginal Relief", preview.marginal_relief_amount, indent=1),
        _row("tax.surcharge", "Surcharge after marginal relief", preview.surcharge_amount, indent=1),
        _row(
            "tax.cess",
            "Add: Health and Education Cess",
            preview.cess_amount,
            indent=1,
        ),
        _row(
            "tax.normal",
            "Tax Payable under Normal Provisions",
            preview.tax_normal,
            emphasis="subtotal",
        ),
    ]
    return TaxStatementSectionOut(key="tax", title="Computation of Tax", rows=rows)


def build_mat_section(
    facts: WorkspaceFacts, preview: TaxPreviewOut
) -> TaxStatementSectionOut | None:
    """Omitted entirely when Section 115JB does not apply this year."""
    if not facts.mat_applicable or preview.tax_mat is None:
        return None
    verdict = (
        "Minimum Alternate Tax applies because it exceeds tax under normal provisions."
        if preview.tax_applied_basis.upper() == "MAT"
        else "Tax under normal provisions applies because it exceeds Minimum Alternate Tax."
    )
    book_profit = (
        q(money(facts.computation.book_profit_115jb))
        if facts.computation.book_profit_115jb is not None
        else None
    )
    rows = [
        _row("mat.book_profit", "Book Profit", book_profit, statutory_ref="Section 115JB"),
        _row("mat.tax", "Tax on Book Profit", preview.tax_mat, indent=1),
        _row("mat.normal", "Tax under Normal Provisions", preview.tax_normal, indent=1),
        _row("mat.basis", "Basis applied", None, emphasis="subtotal", note=verdict),
        _row(
            "mat.credit_created",
            "Minimum Alternate Tax Credit created this year",
            preview.mat_credit_created,
            statutory_ref="Section 115JAA",
            indent=1,
        ),
        _row(
            "mat.credit_utilised",
            "Minimum Alternate Tax Credit set off this year",
            preview.mat_credit_utilised,
            statutory_ref="Section 115JAA",
            indent=1,
        ),
    ]
    return TaxStatementSectionOut(
        key="mat", title="Minimum Alternate Tax Comparison (Section 115JB)", rows=rows
    )


def build_credits_section(facts: WorkspaceFacts, preview: TaxPreviewOut) -> TaxStatementSectionOut:
    kinds: dict[str, tuple[str, Decimal]] = {
        "TDS": ("Tax Deducted at Source", ZERO),
        "TCS": ("Tax Collected at Source", ZERO),
        "AdvanceTax": ("Advance Tax paid", ZERO),
        "SelfAssessment": ("Self-Assessment Tax paid", ZERO),
    }
    for credit in facts.credits:
        if credit.docstatus == DOCSTATUS_CANCELLED:
            continue
        key = str(credit.credit_kind or "TDS")
        label, running = kinds.get(key, (key, ZERO))
        kinds[key] = (label, q(running + money(credit.amount_claimed)))

    rows = [
        _row(f"credit.{key}", label, amount, indent=1)
        for key, (label, amount) in kinds.items()
        if amount != ZERO
    ]
    if not rows:
        rows.append(
            _row("credit.empty", "No taxes paid or deducted have been claimed.", None, indent=1)
        )
    rows.append(
        _row(
            "credit.total",
            "Total taxes already paid and deducted at source",
            preview.credits_total,
            emphasis="subtotal",
        )
    )
    return TaxStatementSectionOut(key="credits", title="Taxes Paid and Credits", rows=rows)


def build_interest_section(preview: TaxPreviewOut) -> TaxStatementSectionOut:
    rows = [
        _row(
            "interest.234a",
            "Interest for Late Filing of Return",
            preview.interest_234a,
            statutory_ref="Section 234A",
            indent=1,
        ),
        _row(
            "interest.234b",
            "Interest for Short Payment of Advance Tax",
            preview.interest_234b,
            statutory_ref="Section 234B",
            indent=1,
        ),
        _row(
            "interest.234c",
            "Interest for Deferment of Advance Tax Instalments",
            preview.interest_234c,
            statutory_ref="Section 234C",
            indent=1,
        ),
        _row("interest.total", "Total interest payable", preview.total_interest, emphasis="subtotal"),
    ]
    return TaxStatementSectionOut(key="interest", title="Interest Payable", rows=rows)


def build_net_section(preview: TaxPreviewOut) -> TaxStatementSectionOut:
    rows = [
        _row("net.tax", "Total tax and interest", preview.total_tax),
        _row("net.credits", "Less: Taxes already paid", preview.credits_total, indent=1),
        _row(
            "net.payable",
            "Net Tax Payable",
            preview.net_payable,
            statutory_ref="Section 288B",
            emphasis="total",
        ),
    ]
    if preview.refund_due > ZERO:
        rows.append(_row("net.refund", "Refund Due", preview.refund_due, emphasis="total"))
    return TaxStatementSectionOut(key="net", title="Net Tax Payable or Refund Due", rows=rows)


def _variance_row(key: str, label: str, current: Decimal, prior: Decimal) -> TaxStatementRowOut:
    difference = q(current - prior)
    direction = "higher than" if difference > ZERO else "lower than" if difference < ZERO else "same as"
    return _row(
        key,
        label,
        difference,
        note=f"{q(current)} this year, {q(prior)} last year — {direction} last year.",
    )


async def _previous_year_result(
    db: AsyncSession, *, company_id: uuid.UUID, previous_ay_code: str | None
) -> TaxComputationResult | None:
    if not previous_ay_code:
        return None
    doc = await db.scalar(
        select(TaxComputation)
        .where(
            TaxComputation.company_id == company_id,
            TaxComputation.ay_code == previous_ay_code,
            TaxComputation.docstatus != DOCSTATUS_CANCELLED,
            TaxComputation.current_run_id.is_not(None),
        )
        .order_by(TaxComputation.creation.desc())
    )
    if doc is None or doc.current_run_id is None:
        return None
    return await db.scalar(
        select(TaxComputationResult).where(
            TaxComputationResult.run_id == doc.current_run_id,
            TaxComputationResult.company_id == company_id,
        )
    )


async def build_statement(
    db: AsyncSession, *, company_id: uuid.UUID, computation_id: uuid.UUID
) -> TaxStatementOut:
    """Assemble the reviewable statement for one computation."""
    # Order matters. ``preview_computation`` ends with a defensive rollback, which expires
    # every ORM instance in the session, so facts collected before it would lazy-load on
    # first attribute access and raise MissingGreenlet inside the synchronous builders.
    preview = await preview_service.preview_computation(
        db, company_id=company_id, computation_id=computation_id, overlay=None
    )
    facts = await validation_service.collect_facts(
        db, company_id=company_id, computation_id=computation_id
    )

    sections = [
        build_income_section(facts),
        build_adjustments_section(facts),
        build_depreciation_section(facts),
        build_setoff_section(facts),
        build_total_income_section(preview),
        build_tax_section(preview),
    ]
    mat_section = build_mat_section(facts, preview)
    if mat_section is not None:
        sections.append(mat_section)
    sections.append(build_credits_section(facts, preview))
    sections.append(build_interest_section(preview))
    sections.append(build_net_section(preview))

    previous_ay = facts.assessment_year.prev_ay_code if facts.assessment_year else None
    variance_previous: list[TaxStatementRowOut] = []
    notes: list[str] = []
    prior = await _previous_year_result(
        db, company_id=company_id, previous_ay_code=previous_ay
    )
    if prior is None:
        notes.append(
            "There is no computed return for the previous assessment year, "
            "so no year-on-year comparison is available."
        )
    else:
        variance_previous = [
            _variance_row(
                "var.total_income",
                "Change in Total Income",
                preview.total_income,
                q(money(prior.taxable_income)),
            ),
            _variance_row(
                "var.tax_normal",
                "Change in Tax under Normal Provisions",
                preview.tax_normal,
                q(money(prior.tax_normal)),
            ),
            _variance_row(
                "var.net_payable",
                "Change in Net Tax Payable",
                preview.net_payable,
                q(money(prior.net_payable)),
            ),
        ]

    variance_books: list[TaxStatementRowOut] = []
    if facts.assessment_year is not None:
        figures = await books_service.read_books(
            db,
            company_id=company_id,
            fy_start=facts.assessment_year.fy_start,
            fy_end=facts.assessment_year.fy_end,
        )
        variance_books = [
            _row("books.net_profit", "Net profit as per the books of account", figures.net_profit),
            _row("books.total_income", "Total Income as computed for tax", preview.total_income),
            _row(
                "books.difference",
                "Difference explained by tax adjustments, depreciation and set-off",
                q(preview.total_income - figures.net_profit),
                emphasis="subtotal",
            ),
            _row(
                "books.accounting_depreciation",
                "Depreciation charged in the books of account",
                figures.accounting_depreciation,
                indent=1,
            ),
        ]

    if not preview.matches_current_run:
        notes.append(
            "These figures are a live calculation of the current worksheet and differ "
            "from the last saved computation run."
        )

    registration = facts.registration
    regime_title = facts.regime.title if facts.regime else facts.computation.regime_code
    entity_title = (
        facts.assessee_class.title if facts.assessee_class else facts.computation.assessee_class_code
    )
    return TaxStatementOut(
        ay_code=facts.computation.ay_code,
        previous_ay_code=previous_ay,
        entity_label=entity_title,
        pan=registration.pan if registration else None,
        regime_label=regime_title,
        basis_applied=preview.tax_applied_basis,
        sections=sections,
        variance_previous_year=variance_previous,
        variance_books=variance_books,
        notes=notes,
    )
