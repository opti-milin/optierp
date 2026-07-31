"""Payroll ↔ Income Tax bridge — contract for HR/Payroll implementers.

CONTRACT for HR/Payroll implementers (do not remove):
1. On Salary Slip submit: append Form16Slice to FY bucket for
   (company_id, employee_id, assessment_year).
2. On "Seed from Payroll" (or Payroll Entry close): call
   seed_individual_computation_from_payroll(...).
3. Map: gross → gross_salary/salary_income; exemptions → exemptions_total;
   taxable → taxable_salary; TDS → tax_deducted/salary_tds; employer TAN/name
   from Company or Payroll Settings.
4. Set seed_source="Payroll", employee_id, payroll_entry_id, salary_slip_ids;
   leave draft for review.
5. Never overwrite a Submitted computation; cancel+recreate or refuse if
   docstatus=1.
6. Reuse the slab engine in income_tax_computation — do not reimplement tax math.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models.compliance import IncomeTaxComputation
from app.schemas.compliance import IncomeTaxComputationCreate
from app.services import income_tax_computation as itr_svc

ZERO = Decimal("0")
Q2 = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


@dataclass
class Form16Slice:
    """One employer's Form 16 contribution for an employee × FY (from Salary Slips)."""

    employee_id: uuid.UUID
    employee_name: str | None = None
    employee_pan: str | None = None
    employer_name: str | None = None
    employer_tan: str | None = None
    employer_address: str | None = None
    gross_salary: Decimal = ZERO
    exemptions_total: Decimal = ZERO
    taxable_salary: Decimal = ZERO
    tax_deducted: Decimal = ZERO
    salary_slip_ids: list[uuid.UUID] = field(default_factory=list)


async def form16_slices_from_salary_slips(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    employee_id: uuid.UUID | None = None,
    payroll_entry_id: uuid.UUID | None = None,
) -> list[Form16Slice]:
    """Aggregate Salary Slips into Form16Slice rows.

    Payroll MUST replace this stub to query Salary Slip / Payroll Entry tables.
    Returns [] until HR/Payroll is built.
    """
    _ = (db, company_id, assessment_year, employee_id, payroll_entry_id)
    return []


def _fy_dates_for_ay(assessment_year: str) -> tuple[date, date]:
    try:
        start_year = int(assessment_year.split("-")[0]) - 1
    except (ValueError, IndexError) as exc:
        raise ValidationError(
            "assessment_year must look like '2025-26'", field="assessment_year"
        ) from exc
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


async def seed_individual_computation_from_payroll(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    assessment_year: str,
    user: CurrentUser,
    employee_id: uuid.UUID | None = None,
    payroll_entry_id: uuid.UUID | None = None,
    slices: list[Form16Slice] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> IncomeTaxComputation:
    """Create/update a **draft** IndividualHeads computation from Form16Slice data.

    When ``slices`` is None, loads via ``form16_slices_from_salary_slips`` (empty until Payroll).
    """
    if slices is None:
        slices = await form16_slices_from_salary_slips(
            db,
            company_id=company_id,
            assessment_year=assessment_year,
            employee_id=employee_id,
            payroll_entry_id=payroll_entry_id,
        )
    if not slices:
        raise ValidationError(
            "Payroll is not enabled or no salary slips were found for this period. "
            "Enter Form 16 / income heads manually, or wire "
            "form16_slices_from_salary_slips when building HR/Payroll.",
            field="payroll",
            code="PAYROLL_NOT_ENABLED",
        )

    # Merge slices for one employee (or first slice when employee_id omitted).
    target_employee = employee_id or slices[0].employee_id
    merged = [s for s in slices if s.employee_id == target_employee]
    if not merged:
        raise ValidationError("No Form 16 slices for the requested employee", field="employee_id")

    gross = sum((_q(s.gross_salary) for s in merged), ZERO)
    exemptions = sum((_q(s.exemptions_total) for s in merged), ZERO)
    taxable = sum((_q(s.taxable_salary) for s in merged), ZERO)
    tds = sum((_q(s.tax_deducted) for s in merged), ZERO)
    slip_ids = [str(sid) for s in merged for sid in s.salary_slip_ids]
    first = merged[0]
    fy_from, fy_to = _fy_dates_for_ay(assessment_year)
    from_date = from_date or fy_from
    to_date = to_date or fy_to

    create = IncomeTaxComputationCreate(
        assessment_year=assessment_year,
        from_date=from_date,
        to_date=to_date,
        assessee_mode="IndividualHeads",
        employee_id=target_employee,
        seed_from_books=False,
        salary_income=taxable,
        salary_tds=tds,
        employer_name=first.employer_name,
        employer_tan=first.employer_tan,
        employer_address=first.employer_address,
        employee_name=first.employee_name,
        employee_pan=first.employee_pan,
        gross_salary=gross,
        exemptions_total=exemptions,
        taxable_salary=taxable,
        tax_deducted=tds,
    )
    # create_computation commits; then patch bridge columns in a follow-up update path.
    # Prefer creating via service then setting payroll fields before final commit —
    # create_computation already commits, so we update after.
    doc = await itr_svc.create_computation(db, create, user)
    # Re-open session state: get + set payroll fields
    doc = await itr_svc.get_computation(db, doc.id, company_id)
    if doc.docstatus != 0:
        raise ValidationError("Cannot attach payroll seed to a non-draft computation")
    doc.seed_source = "Payroll"
    doc.payroll_entry_id = payroll_entry_id
    doc.salary_slip_ids = slip_ids
    doc.modified_by = user.id
    await db.commit()
    return await itr_svc.get_computation(db, doc.id, company_id)
