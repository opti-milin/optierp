"""Structured review of a computation — blocking errors versus advisory warnings.

The workspace navigation and the Review panel must never disagree, so both read the
same ``WorkspaceFacts`` snapshot: ``collect_facts`` loads once, ``build_validation``
classifies, and ``workspace.py`` derives its section statuses from the very same
issue list.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models import statutory as stat
from app.models.assets import Asset
from app.models.base import DOCSTATUS_DRAFT, DOCSTATUS_SUBMITTED
from app.models.tax_computation import (
    TaxComputation,
    TaxComputationAdjustmentLine,
    TaxComputationIncomeLine,
    TaxComputationResult,
    TaxComputationRun,
)
from app.models.tax_config import TaxRegimeElection, TaxRegistration
from app.models.tax_corporate import (
    MatCreditLedger,
    TaxDepreciationRegister,
    TaxLossCarryForward,
    TaxLossSetoffEntry,
)
from app.models.tax_credits import Tax26asReconRun, TaxChallan, TaxCreditEntry
from app.models.tax_filings import TaxFiling
from app.schemas.taxation import (
    AdvanceTaxCalendarOut,
    TaxValidationIssueOut,
    TaxValidationOut,
)
from app.services.taxation import calendar as calendar_service
from app.services.taxation import challans as challan_service
from app.services.taxation import credits as credit_service
from app.services.taxation import depreciation as dep_service
from app.services.taxation import filings as filing_service
from app.services.taxation import interest as interest_service
from app.services.taxation import loss_setoff as loss_service
from app.services.taxation import mat_credit as mat_service
from app.services.taxation import preview as preview_service
from app.services.taxation import registration as registration_service
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.money import ZERO, money, q, rupees
from app.services.taxation.templates import is_concessional, mat_applies

# Heads where a negative net is a real statutory outcome (house property, business, gains).
HEADS_ALLOWING_LOSS = frozenset({"HP", "PGBP", "CG"})
RETURN_DUE_SOON_DAYS = 30

FORFEITED_BY_REGIME: dict[str, tuple[str, ...]] = {
    "115BAA": (
        "Additional depreciation under Section 32(1)(iia)",
        "Most Chapter VI-A deductions other than Section 80JJAA and Section 80M",
        "Set-off of brought forward loss attributable to those deductions",
        "Minimum Alternate Tax under Section 115JB does not apply",
    ),
    "115BAB": (
        "Additional depreciation under Section 32(1)(iia)",
        "Most Chapter VI-A deductions other than Section 80JJAA and Section 80M",
        "Set-off of brought forward loss attributable to those deductions",
        "Minimum Alternate Tax under Section 115JB does not apply",
    ),
    "115BA": (
        "Additional depreciation under Section 32(1)(iia)",
        "Investment allowance and specified area-based deductions",
    ),
    "115BAC": (
        "Most Chapter VI-A deductions",
        "Several exemptions and allowances otherwise available on salary",
    ),
}


@dataclass(frozen=True, slots=True)
class WorkspaceFacts:
    """One read of everything the workspace and the validator both need."""

    computation: TaxComputation
    income_lines: list[TaxComputationIncomeLine]
    adjustment_lines: list[TaxComputationAdjustmentLine]
    registration: TaxRegistration | None
    election: TaxRegimeElection | None
    assessee_class: stat.AssesseeClass | None
    regime: stat.TaxRegime | None
    assessment_year: stat.AssessmentYear | None
    runs: list[TaxComputationRun]
    current_run: TaxComputationRun | None
    result: TaxComputationResult | None
    registers: list[TaxDepreciationRegister]
    depreciation_total: Decimal
    losses: list[TaxLossCarryForward]
    setoff_entries: list[TaxLossSetoffEntry]
    mat_credits: list[MatCreditLedger]
    mat_credit_available: Decimal
    credits: list[TaxCreditEntry]
    challans: list[TaxChallan]
    reconciliations: list[Tax26asReconRun]
    filings: list[TaxFiling]
    advance_tax: AdvanceTaxCalendarOut | None
    asset_count: int
    return_due_date: date | None
    mat_applicable: bool
    concessional_regime: bool
    forfeited_incentives: list[str] = field(default_factory=list)
    expected_ruleset_hash: str | None = None
    expected_input_hash: str | None = None
    today: date = field(default_factory=date.today)

    @property
    def is_draft(self) -> bool:
        return self.computation.docstatus == DOCSTATUS_DRAFT

    @property
    def is_submitted(self) -> bool:
        return self.computation.docstatus == DOCSTATUS_SUBMITTED


async def collect_facts(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
    deep: bool = True,
) -> WorkspaceFacts:
    """Load the computation and everything hanging off its assessment year.

    ``deep=False`` skips the two expensive derivations (recomputing the input
    fingerprint and projecting the advance-tax calendar) for the year list, where a
    per-year deep read would multiply the query count.
    """
    from app.services.taxation import computations as computation_service

    doc = await computation_service.get_computation(db, company_id, computation_id)
    ay_code = doc.ay_code

    registration = await registration_service.get_registration(db, company_id)
    election = await registration_service.get_election(db, company_id, ay_code)
    assessee_class = await db.scalar(
        select(stat.AssesseeClass).where(stat.AssesseeClass.code == doc.assessee_class_code)
    )
    regime = await db.scalar(
        select(stat.TaxRegime).where(
            stat.TaxRegime.code == doc.regime_code,
            stat.TaxRegime.assessee_class_code == doc.assessee_class_code,
        )
    )
    assessment_year = await db.scalar(
        select(stat.AssessmentYear).where(stat.AssessmentYear.code == ay_code)
    )

    runs = sorted(doc.runs, key=lambda r: r.run_no, reverse=True)
    current_run = next((r for r in runs if r.id == doc.current_run_id), None)
    result: TaxComputationResult | None = None
    if current_run is not None:
        result = await db.scalar(
            select(TaxComputationResult).where(
                TaxComputationResult.run_id == current_run.id,
                TaxComputationResult.company_id == company_id,
            )
        )

    registers = await dep_service.list_registers(db, company_id, ay_code=ay_code)
    depreciation_total = await dep_service.total_depreciation(db, company_id, ay_code)
    losses = await loss_service.list_losses(db, company_id)
    setoff_entries = await loss_service.list_setoffs(db, company_id, ay_code=ay_code)
    mat_credits = await mat_service.list_mat_credits(db, company_id)
    mat_credit_available = await mat_service.available_mat_credit(
        db, company_id, current_ay=ay_code
    )
    credits = await credit_service.list_credits(db, company_id, ay_code=ay_code)
    challans = await challan_service.list_challans(db, company_id, ay_code=ay_code)
    reconciliations = await _list_reconciliations(db, company_id, ay_code)
    filings = await filing_service.list_filings(db, company_id, computation_id=doc.id)
    asset_count = int(
        await db.scalar(
            select(func.count(Asset.id)).where(
                Asset.company_id == company_id,
                Asset.docstatus == DOCSTATUS_SUBMITTED,
            )
        )
        or 0
    )

    due_rules = await cat.list_due_date_rules(db, ay_code=ay_code)
    return_due_date = interest_service.resolve_itr_due_date(
        due_rules,
        ay_code=ay_code,
        assessee_class_code=doc.assessee_class_code,
        audit_applicable=bool(doc.audit_applicable),
        override=doc.itr_due_date_override,
    )

    advance_tax: AdvanceTaxCalendarOut | None = None
    expected_ruleset_hash: str | None = None
    expected_input_hash: str | None = None
    if deep:
        advance_tax = await calendar_service.get_advance_tax_calendar(
            db, company_id, ay_code=ay_code, computation_id=doc.id
        )
        expected_ruleset_hash, expected_input_hash = await preview_service.current_fingerprint(
            db, doc
        )

    return WorkspaceFacts(
        computation=doc,
        income_lines=sorted(doc.income_lines, key=lambda ln: ln.seq),
        adjustment_lines=list(doc.adjustment_lines),
        registration=registration,
        election=election,
        assessee_class=assessee_class,
        regime=regime,
        assessment_year=assessment_year,
        runs=runs,
        current_run=current_run,
        result=result,
        registers=registers,
        depreciation_total=depreciation_total,
        losses=losses,
        setoff_entries=setoff_entries,
        mat_credits=mat_credits,
        mat_credit_available=mat_credit_available,
        credits=credits,
        challans=challans,
        reconciliations=reconciliations,
        filings=filings,
        advance_tax=advance_tax,
        asset_count=asset_count,
        return_due_date=return_due_date,
        mat_applicable=mat_applies(doc.assessee_class_code, doc.regime_code),
        concessional_regime=is_concessional(doc.regime_code),
        forfeited_incentives=list(FORFEITED_BY_REGIME.get(doc.regime_code, ())),
        expected_ruleset_hash=expected_ruleset_hash,
        expected_input_hash=expected_input_hash,
    )


async def _list_reconciliations(
    db: AsyncSession, company_id: uuid.UUID, ay_code: str
) -> list[Tax26asReconRun]:
    result = await db.scalars(
        select(Tax26asReconRun)
        .where(
            Tax26asReconRun.company_id == company_id,
            Tax26asReconRun.ay_code == ay_code,
        )
        .order_by(Tax26asReconRun.creation.desc())
    )
    return list(result.all())


def _issue(
    severity: str,
    code: str,
    message: str,
    section: str,
    *,
    field_name: str | None = None,
    hint: str | None = None,
    row_index: int | None = None,
) -> TaxValidationIssueOut:
    return TaxValidationIssueOut(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        section=section,
        field=field_name,
        hint=hint,
        row_index=row_index,
    )


def _registration_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    issues: list[TaxValidationIssueOut] = []
    reg = facts.registration
    if reg is None or not reg.pan:
        issues.append(
            _issue(
                "blocking",
                "registration_missing",
                "The company's Permanent Account Number is not recorded, so a return cannot "
                "be prepared.",
                "overview",
                field_name="pan",
                hint="Enter it under Company Tax Registration.",
            )
        )
        return issues
    klass = facts.assessee_class
    if klass is not None:
        try:
            registration_service.validate_pan_vs_class(
                reg.pan, klass.code, klass.pan_4th_chars or ""
            )
        except ValidationError as exc:
            issues.append(
                _issue(
                    "blocking",
                    exc.code if exc.code != "ERR_VALIDATION" else "pan_class_mismatch",
                    exc.detail,
                    "overview",
                    field_name="pan",
                    hint="Check the Permanent Account Number against the entity class.",
                )
            )
    return issues


def _income_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    issues: list[TaxValidationIssueOut] = []
    if not facts.income_lines:
        issues.append(
            _issue(
                "blocking",
                "no_income_lines",
                "No income has been entered under any head.",
                "income",
                hint="Add at least one income line, or pull the net profit from the books.",
            )
        )
        return issues
    for index, line in enumerate(facts.income_lines):
        if money(line.net) < ZERO and line.head not in HEADS_ALLOWING_LOSS:
            issues.append(
                _issue(
                    "blocking",
                    "income_line_negative_net",
                    f"Row {index + 1} shows a negative net amount under a head that cannot "
                    "return a loss.",
                    "income",
                    field_name="net",
                    row_index=index,
                    hint="Check the gross amount and the deductions on this row.",
                )
            )
    return issues


def _regime_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    if not facts.concessional_regime or facts.election is not None:
        return []
    return [
        _issue(
            "blocking",
            "regime_election_missing",
            f"The concessional rate under Section {facts.computation.regime_code} has been "
            "applied but no election is on record for this assessment year.",
            "overview",
            field_name="regime_code",
            hint="Record the election, including the form acknowledgement number.",
        )
    ]


def _mat_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    if not facts.mat_applicable or facts.computation.book_profit_115jb is not None:
        return []
    message = (
        "Minimum Alternate Tax under Section 115JB applies to this company but the book "
        "profit has not been entered, so the comparison has been skipped."
    )
    issues = [
        _issue(
            "advisory",
            "book_profit_required",
            message,
            "mat",
            field_name="book_profit_115jb",
            hint="Pull the net profit from the books, or enter the book profit.",
        )
    ]
    if facts.computation.docstatus == DOCSTATUS_SUBMITTED:
        issues.append(
            _issue(
                "blocking",
                "book_profit_required_for_filing",
                "The book profit is required before this return can be filed, because "
                "Minimum Alternate Tax under Section 115JB applies.",
                "mat",
                field_name="book_profit_115jb",
            )
        )
    return issues


def _run_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    if facts.current_run is None or facts.result is None:
        return [
            _issue(
                "blocking",
                "no_run",
                "The tax has never been computed for this worksheet.",
                "review",
                hint="Compute the tax so the figures can be reviewed and filed.",
            )
        ]
    if facts.expected_input_hash is None:
        return []
    stale = (
        facts.current_run.input_hash != facts.expected_input_hash
        or facts.current_run.ruleset_hash != facts.expected_ruleset_hash
    )
    if not stale:
        return []
    return [
        _issue(
            "blocking",
            "stale_run",
            "The worksheet has changed since the tax was last computed, so the figures on "
            "record are out of date.",
            "review",
            hint="Compute the tax again to refresh the result.",
        )
    ]


def _challan_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    drafts = [c for c in facts.challans if c.docstatus == DOCSTATUS_DRAFT]
    if not drafts:
        return []
    return [
        _issue(
            "blocking",
            "challan_unsubmitted",
            f"{len(drafts)} tax payment challan(s) are still in draft, so the payment has not "
            "reached the ledger and cannot be relied on.",
            "challans",
            hint="Submit each challan once the payment is confirmed.",
        )
    ]


def _credit_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    issues: list[TaxValidationIssueOut] = []
    for index, row in enumerate(facts.credits):
        if money(row.amount_claimed) > money(row.amount_credited):
            issues.append(
                _issue(
                    "blocking",
                    "credit_claimed_exceeds_credited",
                    f"Row {index + 1} claims more credit than the amount actually credited.",
                    "credits",
                    field_name="amount_claimed",
                    row_index=index,
                )
            )
    return issues


def _advisory_issues(facts: WorkspaceFacts) -> list[TaxValidationIssueOut]:
    issues: list[TaxValidationIssueOut] = []
    ay_code = facts.computation.ay_code

    if facts.asset_count > 0 and not facts.registers:
        issues.append(
            _issue(
                "advisory",
                "depreciation_not_synced",
                f"{facts.asset_count} asset(s) are on the fixed asset register but no "
                "depreciation register exists for this assessment year.",
                "depreciation",
                hint="Rebuild the register from the assets to claim depreciation.",
            )
        )

    expiring = [
        row
        for row in facts.losses
        if row.expires_after_ay == ay_code and money(row.amount_remaining) > ZERO
    ]
    if expiring:
        issues.append(
            _issue(
                "advisory",
                "losses_expiring",
                f"{len(expiring)} brought forward loss entr(ies) lapse after this assessment "
                "year and have not been fully set off.",
                "losses",
                hint="Set them off this year or the balance is lost.",
            )
        )

    mat_expiring = [
        row
        for row in facts.mat_credits
        if row.entry_kind == "Created" and row.expires_after_ay == ay_code
    ]
    if mat_expiring and facts.mat_credit_available > ZERO:
        issues.append(
            _issue(
                "advisory",
                "mat_credit_expiring",
                "Minimum Alternate Tax credit under Section 115JAA lapses after this "
                "assessment year and has not been fully set off.",
                "mat",
            )
        )

    if facts.credits and not facts.reconciliations:
        issues.append(
            _issue(
                "advisory",
                "form26as_not_reconciled",
                "Tax credits have been claimed but they have not been reconciled with "
                "Form 26AS for this assessment year.",
                "reconciliation",
                hint="Upload the portal statement and match it against the books.",
            )
        )

    if facts.advance_tax is not None:
        short = [i for i in facts.advance_tax.instalments if i.shortfall > ZERO]
        if short:
            total_short = q(sum((money(i.shortfall) for i in short), ZERO))
            issues.append(
                _issue(
                    "advisory",
                    "advance_tax_shortfall",
                    f"{len(short)} advance tax instalment(s) are short by "
                    f"{rupees(total_short)} in total.",
                    "interest",
                    hint="Paying the shortfall reduces interest under Sections 234B and 234C.",
                )
            )

    filed = facts.computation.return_filed_date
    if filed is not None and facts.return_due_date is not None and filed > facts.return_due_date:
        issues.append(
            _issue(
                "advisory",
                "interest_234a_applicable",
                f"The return was filed on {filed.isoformat()}, after the due date of "
                f"{facts.return_due_date.isoformat()}, so interest for late filing applies.",
                "interest",
                hint="Interest under Section 234A has been included in the computation.",
            )
        )

    if facts.return_due_date is not None and not facts.filings:
        days = (facts.return_due_date - facts.today).days
        if 0 <= days <= RETURN_DUE_SOON_DAYS:
            issues.append(
                _issue(
                    "advisory",
                    "return_due_soon",
                    f"The return is due on {facts.return_due_date.isoformat()}, in {days} day(s), "
                    "and nothing has been filed yet.",
                    "filing",
                )
            )

    if facts.is_submitted and facts.current_run is not None and not facts.filings:
        issues.append(
            _issue(
                "advisory",
                "no_filing_generated",
                "The computation has been submitted but no return has been generated from it.",
                "filing",
                hint="Generate the ITR-6 return to produce the filing payload.",
            )
        )
    return issues


def build_validation(facts: WorkspaceFacts) -> TaxValidationOut:
    """Classify the snapshot into blocking errors and advisory warnings."""
    issues: list[TaxValidationIssueOut] = []
    issues.extend(_registration_issues(facts))
    issues.extend(_income_issues(facts))
    issues.extend(_regime_issues(facts))
    issues.extend(_mat_issues(facts))
    issues.extend(_run_issues(facts))
    issues.extend(_challan_issues(facts))
    issues.extend(_credit_issues(facts))
    issues.extend(_advisory_issues(facts))

    blocking = sum(1 for i in issues if i.severity == "blocking")
    advisory = len(issues) - blocking
    return TaxValidationOut(
        ok=blocking == 0,
        blocking_count=blocking,
        advisory_count=advisory,
        issues=issues,
    )


async def validate_computation(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
    deep: bool = True,
) -> TaxValidationOut:
    facts = await collect_facts(
        db, company_id=company_id, computation_id=computation_id, deep=deep
    )
    return build_validation(facts)
