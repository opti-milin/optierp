"""Lean Form 16 PDF from an Income Tax Computation (IndividualHeads)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.pdf import html_to_pdf, render_print_format
from app.services.accounts_common import get_company
from app.services.income_tax_computation import get_computation

ZERO = Decimal("0")
Q2 = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


async def form_16_pdf(
    db: AsyncSession, doc_id: uuid.UUID, company_id: uuid.UUID
) -> bytes:
    """Render a lean Form 16 Part A/B handoff PDF (not CPC XML)."""
    doc = await get_computation(db, doc_id, company_id)
    if doc.assessee_mode != "IndividualHeads":
        raise ValidationError(
            "Form 16 PDF is for IndividualHeads computations only",
            field="assessee_mode",
        )
    company = await get_company(db, company_id)
    html = render_print_format(
        "form_16.html",
        {
            "assessment_year": doc.assessment_year,
            "from_date": doc.from_date.isoformat(),
            "to_date": doc.to_date.isoformat(),
            "employer_name": doc.employer_name or company.company_name,
            "employer_tan": doc.employer_tan or company.tan or "",
            "employer_address": doc.employer_address or "",
            "employee_name": doc.employee_name or "",
            "employee_pan": doc.employee_pan or "",
            "gross_salary": str(_q(doc.gross_salary)),
            "exemptions_total": str(_q(doc.exemptions_total)),
            "standard_deduction": str(_q(doc.standard_deduction)),
            "taxable_salary": str(_q(doc.taxable_salary or doc.salary_income)),
            "tax_deducted": str(_q(doc.tax_deducted or doc.salary_tds)),
            "tax_amount": str(_q(doc.tax_amount)),
            "cess_amount": str(_q(doc.cess_amount)),
            "total_tax": str(_q(doc.total_tax)),
            "tax_payable": str(_q(doc.tax_payable)),
            "computation_ref": doc.name,
            "company_name": company.company_name,
        },
    )
    return html_to_pdf(html)
