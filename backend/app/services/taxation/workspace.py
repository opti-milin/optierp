"""Backend-for-frontend aggregate behind the unified Income Tax workspace.

The workspace renders fourteen sections from one round trip. Everything here is
assembled from the single ``WorkspaceFacts`` read that the validator already
performs, so the section badges and the Review panel can never disagree: both
are derived from the same issue list.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import statutory as stat
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED
from app.models.tax_computation import TaxComputation, TaxComputationResult
from app.schemas.taxation import (
    TaxSectionStatusOut,
    TaxValidationIssueOut,
    TaxValidationOut,
    TaxWorkspaceBootstrapOut,
    TaxWorkspaceContextOut,
    TaxWorkspaceOut,
    TaxYearSummaryOut,
)
from app.services.taxation import challans as challan_service
from app.services.taxation import computations as computation_service
from app.services.taxation import credits as credit_service
from app.services.taxation import depreciation as dep_service
from app.services.taxation import filings as filing_service
from app.services.taxation import loss_setoff as loss_service
from app.services.taxation import lookups as lookup_service
from app.services.taxation import mat_credit as mat_service
from app.services.taxation import recon_26as as recon_service
from app.services.taxation import registration as registration_service
from app.services.taxation import templates as template_service
from app.services.taxation import validation as validation_service
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.money import ZERO, money, q, rupees
from app.services.taxation.lookups import ay_label, regime_label
from app.services.taxation.validation import WorkspaceFacts

SECTION_LABELS: dict[str, str] = {
    "overview": "Setup & Basis",
    "income": "Statement of Income",
    "adjustments": "Tax Adjustments",
    "depreciation": "Depreciation (Income-tax Act)",
    "losses": "Brought Forward Losses & Set-off",
    "mat": "Minimum Alternate Tax (Section 115JB)",
    "credits": "Taxes Already Paid",
    "challans": "Tax Payment Challans",
    "reconciliation": "Reconcile with Form 26AS",
    "interest": "Advance Tax & Interest",
    "summary": "Statement of Total Income",
    "review": "Review & Validate",
    "filing": "Return Filing (ITR-6)",
    "audit": "Computation History",
}

SECTION_ORDER: tuple[str, ...] = (
    "overview",
    "income",
    "adjustments",
    "depreciation",
    "losses",
    "mat",
    "credits",
    "challans",
    "reconciliation",
    "interest",
    "summary",
    "review",
    "filing",
    "audit",
)

# Years shown on the landing screen, newest first.
LANDING_YEAR_LIMIT = 6


@dataclass(frozen=True, slots=True)
class _SectionFacts:
    """Row count plus a one-line human summary for a single section."""

    rows: int
    summary: str | None
    started: bool
    complete: bool


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _rupees(amount: Decimal | None) -> str:
    """Section-navigation summaries read like a Chartered Accountant's note, not a dump."""
    return rupees(amount)


def build_context(facts: WorkspaceFacts) -> TaxWorkspaceContextOut:
    """The typed object every context-aware field rule reads on the client."""
    doc = facts.computation
    year = facts.assessment_year
    fy_start = year.fy_start if year else date(2000, 4, 1)
    fy_end = year.fy_end if year else date(2001, 3, 31)
    return TaxWorkspaceContextOut(
        ay_code=doc.ay_code,
        ay_label=ay_label(doc.ay_code, year.fy_start.year if year else None),
        previous_ay_code=year.prev_ay_code if year else None,
        financial_year_label=f"Financial Year {fy_start.year}-{str(fy_end.year)[-2:]}",
        fy_start=fy_start,
        fy_end=fy_end,
        entity_class_code=doc.assessee_class_code,
        entity_class_label=(
            facts.assessee_class.title if facts.assessee_class else doc.assessee_class_code
        ),
        regime_code=doc.regime_code,
        regime_label=regime_label(
            doc.regime_code, facts.regime.title if facts.regime else None
        ),
        regime_statutory_ref=(
            f"Section {doc.regime_code}" if doc.regime_code.startswith("115") else None
        ),
        regime_irrevocable=bool(facts.election.irrevocable) if facts.election else False,
        concessional_regime=facts.concessional_regime,
        forfeited_incentives=list(facts.forfeited_incentives),
        mat_applicable=facts.mat_applicable,
        # A presumptive election is not modelled for corporate assessees, which are the
        # only classes the workspace currently computes; the flag stays false until it is.
        presumptive=False,
        audit_applicable=bool(doc.audit_applicable),
        filing_type=str(doc.filing_type or "Original"),
        itr_form_code=(
            facts.filings[0].form_code
            if facts.filings
            else (facts.assessee_class.default_itr_form if facts.assessee_class else None)
        ),
        return_due_date=facts.return_due_date,
        is_draft=doc.docstatus == DOCSTATUS_DRAFT,
        is_submitted=doc.docstatus == DOCSTATUS_SUBMITTED,
        is_cancelled=doc.docstatus == DOCSTATUS_CANCELLED,
        currency="INR",
    )


def _overview_facts(facts: WorkspaceFacts) -> _SectionFacts:
    reg = facts.registration
    if reg is None or not reg.pan:
        return _SectionFacts(0, "Company tax registration is not set up yet.", False, False)
    return _SectionFacts(
        1,
        f"{facts.computation.assessee_class_code} on the "
        f"{regime_label(facts.computation.regime_code, facts.regime.title if facts.regime else None)}",
        True,
        True,
    )


def _income_facts(facts: WorkspaceFacts) -> _SectionFacts:
    rows = len(facts.income_lines)
    if rows == 0:
        return _SectionFacts(0, "No income entered.", False, False)
    total = q(sum((money(ln.net) for ln in facts.income_lines), ZERO))
    heads = len({ln.head for ln in facts.income_lines})
    return _SectionFacts(
        rows,
        f"{_plural(heads, 'head')} of income, gross total {_rupees(total)}",
        True,
        True,
    )


def _adjustment_facts(facts: WorkspaceFacts) -> _SectionFacts:
    draft = [ln for ln in facts.adjustment_lines if ln.run_id is None]
    if not draft:
        return _SectionFacts(0, "No adjustments recorded.", False, True)
    net = ZERO
    for line in draft:
        amount = money(line.override_amount if line.override_amount is not None else line.amount)
        if str(line.direction or "Add").lower() in ("deduct", "less", "deduction"):
            net = q(net - amount)
        else:
            net = q(net + amount)
    return _SectionFacts(
        len(draft), f"{_plural(len(draft), 'adjustment')}, net {_rupees(net)}", True, True
    )


def _depreciation_facts(facts: WorkspaceFacts) -> _SectionFacts:
    rows = len(facts.registers)
    if rows == 0:
        summary = (
            f"No depreciation blocks prepared, though {_plural(facts.asset_count, 'fixed asset')} "
            "exist."
            if facts.asset_count
            else "No depreciation blocks prepared."
        )
        return _SectionFacts(0, summary, False, facts.asset_count == 0)
    return _SectionFacts(
        rows,
        f"{_plural(rows, 'block')}, depreciation claimed {_rupees(facts.depreciation_total)}",
        True,
        True,
    )


def _loss_facts(facts: WorkspaceFacts) -> _SectionFacts:
    rows = len(facts.losses)
    if rows == 0:
        return _SectionFacts(0, "No brought forward losses on record.", False, True)
    # amount_remaining is already net of this year's set-off, so it is what carries forward.
    carried_forward = q(sum((money(row.amount_remaining) for row in facts.losses), ZERO))
    # Only the current run's active applications — prior runs that were reversed for a
    # recompute must not inflate the "set off this year" figure.
    current_run_id = facts.computation.current_run_id
    set_off = ZERO
    for entry in facts.setoff_entries:
        entry_computation_id = getattr(entry, "computation_id", None)
        if entry_computation_id is not None and entry_computation_id != facts.computation.id:
            continue
        explanation = getattr(entry, "explanation", None) or {}
        if bool(explanation.get("reversed")):
            continue
        entry_run_id = getattr(entry, "run_id", None)
        if current_run_id is not None and entry_run_id not in (None, current_run_id):
            continue
        set_off = q(set_off + money(entry.amount_set_off))
    return _SectionFacts(
        rows,
        f"{_rupees(set_off)} set off this year, {_rupees(carried_forward)} carried forward",
        True,
        True,
    )


def _mat_facts(facts: WorkspaceFacts) -> _SectionFacts:
    if not facts.mat_applicable:
        return _SectionFacts(
            len(facts.mat_credits),
            "Section 115JB does not apply under the elected regime.",
            True,
            True,
        )
    book_profit = facts.computation.book_profit_115jb
    if book_profit is None:
        return _SectionFacts(
            len(facts.mat_credits), "Book profit has not been entered.", False, False
        )
    return _SectionFacts(
        len(facts.mat_credits),
        f"Book profit {_rupees(q(money(book_profit)))}, "
        f"credit available {_rupees(facts.mat_credit_available)}",
        True,
        True,
    )


def _credit_facts(facts: WorkspaceFacts) -> _SectionFacts:
    live = [c for c in facts.credits if c.docstatus != DOCSTATUS_CANCELLED]
    if not live:
        return _SectionFacts(0, "No taxes paid or deducted claimed.", False, False)
    total = q(sum((money(c.amount_claimed) for c in live), ZERO))
    return _SectionFacts(
        len(live),
        f"{_plural(len(live), 'entry', 'entries')}, {_rupees(total)} claimed",
        True,
        True,
    )


def _challan_facts(facts: WorkspaceFacts) -> _SectionFacts:
    live = [c for c in facts.challans if c.docstatus != DOCSTATUS_CANCELLED]
    if not live:
        return _SectionFacts(0, "No challans recorded.", False, True)
    total = q(sum((money(c.amount) for c in live), ZERO))
    unposted = sum(1 for c in live if c.docstatus == DOCSTATUS_DRAFT)
    summary = f"{_plural(len(live), 'challan')}, {_rupees(total)} deposited"
    if unposted:
        summary += f" — {unposted} not yet posted to the ledger"
    return _SectionFacts(len(live), summary, True, unposted == 0)


def _reconciliation_facts(facts: WorkspaceFacts) -> _SectionFacts:
    rows = len(facts.reconciliations)
    if rows == 0:
        return _SectionFacts(0, "Form 26AS has not been reconciled.", False, False)
    latest = facts.reconciliations[0]
    # difference is Form 26AS less the books, so the sign says which side is short.
    difference = q(money(latest.difference))
    reconciled = difference == ZERO
    if reconciled:
        detail = "books agree with Form 26AS"
    elif difference < ZERO:
        detail = f"books claim {_rupees(-difference)} more than Form 26AS supports"
    else:
        detail = f"{_rupees(difference)} in Form 26AS is not yet claimed"
    summary = f"Last reconciled {latest.creation.date()} — {detail}"
    return _SectionFacts(rows, summary, True, reconciled)


def _interest_facts(facts: WorkspaceFacts) -> _SectionFacts:
    result = facts.result
    if result is None:
        return _SectionFacts(0, "Calculate the return to see interest.", False, False)
    total = q(
        money(result.interest_234a) + money(result.interest_234b) + money(result.interest_234c)
    )
    if total == ZERO:
        return _SectionFacts(0, "No interest payable.", True, True)
    return _SectionFacts(3, f"Interest payable {_rupees(total)}", True, True)


def _summary_facts(facts: WorkspaceFacts) -> _SectionFacts:
    result = facts.result
    if result is None:
        return _SectionFacts(0, "The return has not been calculated yet.", False, False)
    return _SectionFacts(
        1,
        f"Total Income {_rupees(q(money(result.taxable_income)))}, "
        f"Net Tax Payable {_rupees(q(money(result.net_payable)))}",
        True,
        True,
    )


def _filing_facts(facts: WorkspaceFacts) -> _SectionFacts:
    rows = len(facts.filings)
    if rows == 0:
        return _SectionFacts(0, "The return has not been generated.", False, False)
    latest = facts.filings[0]
    ack = f", acknowledgement {latest.ack_no}" if latest.ack_no else ""
    return _SectionFacts(rows, f"{latest.status}{ack}", True, bool(latest.ack_no))


def _audit_facts(facts: WorkspaceFacts) -> _SectionFacts:
    rows = len(facts.runs)
    if rows == 0:
        return _SectionFacts(0, "No computation has been run yet.", False, False)
    return _SectionFacts(rows, f"{_plural(rows, 'run')} recorded", True, True)


_SECTION_BUILDERS = {
    "overview": _overview_facts,
    "income": _income_facts,
    "adjustments": _adjustment_facts,
    "depreciation": _depreciation_facts,
    "losses": _loss_facts,
    "mat": _mat_facts,
    "credits": _credit_facts,
    "challans": _challan_facts,
    "reconciliation": _reconciliation_facts,
    "interest": _interest_facts,
    "summary": _summary_facts,
    "filing": _filing_facts,
    "audit": _audit_facts,
}


def build_sections(
    facts: WorkspaceFacts, validation: TaxValidationOut
) -> list[TaxSectionStatusOut]:
    """Per-section badge: row count, one-line summary, and issue counts."""
    by_section: dict[str, list[TaxValidationIssueOut]] = {}
    for issue in validation.issues:
        by_section.setdefault(issue.section, []).append(issue)

    out: list[TaxSectionStatusOut] = []
    for key in SECTION_ORDER:
        if key == "mat" and not facts.mat_applicable and not facts.mat_credits:
            continue
        issues = by_section.get(key, [])
        blocking = sum(1 for i in issues if i.severity == "blocking")
        advisory = len(issues) - blocking

        if key == "review":
            status = "has-errors" if validation.blocking_count else "complete"
            summary = (
                f"{_plural(validation.blocking_count, 'blocking issue')} and "
                f"{_plural(validation.advisory_count, 'advisory warning')}"
            )
            out.append(
                TaxSectionStatusOut(
                    key=key,
                    label=SECTION_LABELS[key],
                    status=status,  # type: ignore[arg-type]
                    summary=summary,
                    row_count=len(validation.issues),
                    blocking_count=validation.blocking_count,
                    advisory_count=validation.advisory_count,
                )
            )
            continue

        section_facts = _SECTION_BUILDERS[key](facts)
        # ``complete`` is checked before ``started`` on purpose: a section with no rows
        # can still be finished, because a company may genuinely have no adjustments,
        # no brought forward losses and no challans to record.
        if blocking:
            status = "has-errors"
        elif section_facts.complete:
            status = "complete"
        elif section_facts.started:
            status = "in-progress"
        else:
            status = "not-started"

        out.append(
            TaxSectionStatusOut(
                key=key,
                label=SECTION_LABELS[key],
                status=status,  # type: ignore[arg-type]
                summary=section_facts.summary,
                row_count=section_facts.rows,
                blocking_count=blocking,
                advisory_count=advisory,
            )
        )
    return out


async def get_workspace(
    db: AsyncSession, *, company_id: uuid.UUID, computation_id: uuid.UUID
) -> TaxWorkspaceOut:
    """Everything the workspace renders, from one facts read and one lookups build."""
    facts = await validation_service.collect_facts(
        db, company_id=company_id, computation_id=computation_id
    )
    validation = validation_service.build_validation(facts)
    lookups = await lookup_service.build_lookups(
        db, company_id=company_id, ay_code=facts.computation.ay_code
    )

    return TaxWorkspaceOut(
        computation=computation_service.computation_out(facts.computation),
        context=build_context(facts),
        registration=(
            registration_service.registration_out(facts.registration)
            if facts.registration
            else None
        ),
        election=(
            registration_service.election_out(facts.election) if facts.election else None
        ),
        result=computation_service.result_out(facts.result),
        runs=[computation_service.run_out(r) for r in facts.runs],
        depreciation_registers=[dep_service.register_out(r) for r in facts.registers],
        depreciation_total=facts.depreciation_total,
        losses=[loss_service.loss_out(r) for r in facts.losses],
        setoff_entries=[loss_service.setoff_out(r) for r in facts.setoff_entries],
        mat_credits=[mat_service.mat_credit_out(r) for r in facts.mat_credits],
        mat_credit_available=facts.mat_credit_available,
        credits=[credit_service.credit_out(r) for r in facts.credits],
        challans=[challan_service.challan_out(r) for r in facts.challans],
        reconciliations=[recon_service.recon_out(r) for r in facts.reconciliations],
        advance_tax=facts.advance_tax,
        filings=[filing_service.filing_out(r, include_payload=False) for r in facts.filings],
        sections=build_sections(facts, validation),
        validation=validation,
        lookups=lookups,
    )


def _next_action(
    sections: Sequence[TaxSectionStatusOut], docstatus: int
) -> tuple[str, str]:
    """First unfinished section becomes the call to action on the landing card."""
    if docstatus == DOCSTATUS_CANCELLED:
        return "Cancelled — start a fresh computation", "overview"
    for section in sections:
        if section.status == "has-errors":
            return f"Resolve issues in {section.label}", section.key
    for section in sections:
        if section.status == "not-started":
            return f"Complete {section.label}", section.key
    if docstatus == DOCSTATUS_DRAFT:
        return "Review and submit the computation", "review"
    return "File the return", "filing"


def _status_label(docstatus: int, has_run: bool) -> str:
    if docstatus == DOCSTATUS_CANCELLED:
        return "Cancelled"
    if docstatus == DOCSTATUS_SUBMITTED:
        return "Submitted"
    return "In progress" if has_run else "Draft"


async def _year_rows(db: AsyncSession, limit: int) -> list[stat.AssessmentYear]:
    result = await db.scalars(
        select(stat.AssessmentYear).order_by(stat.AssessmentYear.code.desc()).limit(limit)
    )
    return list(result.all())


async def _computations_by_year(
    db: AsyncSession, company_id: uuid.UUID, ay_codes: Sequence[str]
) -> dict[str, TaxComputation]:
    """Latest non-cancelled computation per assessment year, in one query."""
    if not ay_codes:
        return {}
    result = await db.scalars(
        select(TaxComputation)
        .where(
            TaxComputation.company_id == company_id,
            TaxComputation.ay_code.in_(list(ay_codes)),
            TaxComputation.docstatus != DOCSTATUS_CANCELLED,
        )
        .options(selectinload(TaxComputation.income_lines))
        .order_by(TaxComputation.creation.desc())
    )
    out: dict[str, TaxComputation] = {}
    for doc in result.all():
        out.setdefault(doc.ay_code, doc)
    return out


async def _results_by_run(
    db: AsyncSession, company_id: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, TaxComputationResult]:
    if not run_ids:
        return {}
    result = await db.scalars(
        select(TaxComputationResult).where(
            TaxComputationResult.company_id == company_id,
            TaxComputationResult.run_id.in_(list(run_ids)),
        )
    )
    return {row.run_id: row for row in result.all()}


async def build_year_summaries(
    db: AsyncSession, *, company_id: uuid.UUID, current_ay_code: str, limit: int = LANDING_YEAR_LIMIT
) -> list[TaxYearSummaryOut]:
    """One card per assessment year for the landing screen — status, dues, next action."""
    years = await _year_rows(db, limit)
    docs = await _computations_by_year(db, company_id, [y.code for y in years])
    run_ids = [d.current_run_id for d in docs.values() if d.current_run_id is not None]
    results = await _results_by_run(db, company_id, run_ids)

    summaries: list[TaxYearSummaryOut] = []
    for year in years:
        doc = docs.get(year.code)
        label = ay_label(year.code, year.fy_start.year)
        fy_label = f"Financial Year {year.fy_start.year}-{str(year.fy_end.year)[-2:]}"
        if doc is None:
            summaries.append(
                TaxYearSummaryOut(
                    ay_code=year.code,
                    ay_label=label,
                    financial_year_label=fy_label,
                    status="Not started",
                    next_action="Start the computation",
                    next_action_section="overview",
                    is_current=year.code == current_ay_code,
                )
            )
            continue

        facts = await validation_service.collect_facts(
            db, company_id=company_id, computation_id=doc.id, deep=False
        )
        validation = validation_service.build_validation(facts)
        sections = build_sections(facts, validation)
        action, action_section = _next_action(sections, doc.docstatus)
        result = results.get(doc.current_run_id) if doc.current_run_id else None
        taxes_paid = q(
            sum(
                (money(c.amount_claimed) for c in facts.credits if c.docstatus != DOCSTATUS_CANCELLED),
                ZERO,
            )
        )
        latest_filing = facts.filings[0] if facts.filings else None
        next_due_label, next_due_date = _next_due(facts, year)

        summaries.append(
            TaxYearSummaryOut(
                ay_code=year.code,
                ay_label=label,
                financial_year_label=fy_label,
                computation_id=doc.id,
                computation_name=doc.name,
                status=_status_label(doc.docstatus, doc.current_run_id is not None),
                docstatus=doc.docstatus,
                regime_code=doc.regime_code,
                regime_label=regime_label(
                    doc.regime_code, facts.regime.title if facts.regime else None
                ),
                total_income=q(money(result.taxable_income)) if result else None,
                net_payable=q(money(result.net_payable)) if result else None,
                taxes_paid=taxes_paid,
                return_due_date=facts.return_due_date,
                next_due_label=next_due_label,
                next_due_date=next_due_date,
                next_action=action,
                next_action_section=action_section,
                blocking_count=validation.blocking_count,
                advisory_count=validation.advisory_count,
                filing_status=latest_filing.status if latest_filing else None,
                ack_no=latest_filing.ack_no if latest_filing else None,
                is_current=year.code == current_ay_code,
            )
        )
    return summaries


def _next_due(
    facts: WorkspaceFacts, year: stat.AssessmentYear
) -> tuple[str | None, date | None]:
    """Nearest upcoming statutory date — an advance-tax instalment or the return itself."""
    today = facts.today
    candidates: list[tuple[str, date]] = []
    if facts.advance_tax is not None:
        for instalment in facts.advance_tax.instalments:
            if instalment.due_date >= today and instalment.shortfall > ZERO:
                candidates.append(
                    (f"Advance Tax instalment ({instalment.label})", instalment.due_date)
                )
    if facts.return_due_date is not None and facts.return_due_date >= today:
        candidates.append(("Return filing due", facts.return_due_date))
    if not candidates:
        return None, None
    candidates.sort(key=lambda pair: pair[1])
    return candidates[0]


async def bootstrap(
    db: AsyncSession, *, company_id: uuid.UUID, ay_code: str | None = None
) -> TaxWorkspaceBootstrapOut:
    """Cold start: which years exist, where each stands, and how to begin a new one."""
    registration = await registration_service.get_registration(db, company_id)
    years = await cat.list_assessment_years(db)
    current = _current_ay(years)
    default_ay = (
        ay_code
        or (registration.default_assessment_year if registration else None)
        or current
    )

    summaries = await build_year_summaries(
        db, company_id=company_id, current_ay_code=current
    )
    templates = await template_service.list_templates(
        db, company_id=company_id, ay_code=default_ay
    )
    lookups = await lookup_service.build_lookups(db, company_id=company_id, ay_code=default_ay)

    return TaxWorkspaceBootstrapOut(
        current_ay_code=current,
        default_ay_code=default_ay,
        registration=(
            registration_service.registration_out(registration) if registration else None
        ),
        registration_complete=bool(registration and registration.pan),
        years=summaries,
        templates=templates,
        lookups=lookups,
    )


def _current_ay(years: Sequence[stat.AssessmentYear]) -> str:
    """Assessment year whose previous year has already ended, else the newest known."""
    today = date.today()
    ended = [y for y in years if y.fy_end <= today]
    if ended:
        return max(ended, key=lambda y: y.fy_end).code
    if years:
        return min(years, key=lambda y: y.fy_start).code
    return ""
