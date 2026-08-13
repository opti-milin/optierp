"""Pull the year's profit and accounting depreciation out of the general ledger.

The chartered accountant should never retype the net profit that the books already
know. This reads the trial balance for the assessment year's financial year window
and writes it onto the draft worksheet through the normal computation update path,
so the draft-only guard and company scoping still apply.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser
from app.models import statutory as stat
from app.models.tax_computation import TaxComputation
from app.schemas.taxation import (
    TaxComputationAdjustmentLineIn,
    TaxComputationIncomeLineIn,
    TaxComputationUpdate,
    TaxPopulateFromBooksIn,
    TaxPopulateFromBooksOut,
)
from app.services.financial_reports._helpers import _account_map, _balances
from app.services.financial_reports.statements import profit_and_loss
from app.services.taxation import computations as computation_service
from app.services.taxation.kernel.money import ZERO, money, q

ACCOUNTING_DEPRECIATION_ACCOUNT_TYPE = "Depreciation"
BOOK_PROFIT_SUB_REF = "Net profit as per books"
BOOK_PROFIT_SOURCE_DOC_TYPE = "Profit and Loss"
DEPRECIATION_ADDBACK_SECTION = "32"
DEPRECIATION_ADDBACK_DESCRIPTION = (
    "Depreciation as per books added back; Income-tax Act depreciation claimed separately"
)


@dataclass(frozen=True, slots=True)
class BooksFigures:
    """What the general ledger says for one financial year."""

    fy_start: date
    fy_end: date
    net_profit: Decimal
    accounting_depreciation: Decimal


async def assessment_year_window(db: AsyncSession, ay_code: str) -> tuple[date, date]:
    row = await db.scalar(select(stat.AssessmentYear).where(stat.AssessmentYear.code == ay_code))
    if row is None:
        raise ValidationError(
            f"Unknown assessment year '{ay_code}'", code="unknown_ay", field="ay_code"
        )
    return row.fy_start, row.fy_end


async def read_books(
    db: AsyncSession, *, company_id: uuid.UUID, fy_start: date, fy_end: date
) -> BooksFigures:
    """Net profit and accounting depreciation from posted general ledger entries."""
    statement = await profit_and_loss(db, company_id, from_date=fy_start, to_date=fy_end)
    net_profit = q(money(statement["net_profit"]))
    accounting_depreciation = await accounting_depreciation_total(
        db, company_id=company_id, fy_start=fy_start, fy_end=fy_end
    )
    return BooksFigures(
        fy_start=fy_start,
        fy_end=fy_end,
        net_profit=net_profit,
        accounting_depreciation=accounting_depreciation,
    )


async def accounting_depreciation_total(
    db: AsyncSession, *, company_id: uuid.UUID, fy_start: date, fy_end: date
) -> Decimal:
    """Debit-less-credit movement on Depreciation-typed leaf accounts in the window."""
    accounts = await _account_map(db, company_id)
    balances = await _balances(db, company_id, from_date=fy_start, to_date=fy_end)
    total = ZERO
    for account_id, (debit, credit) in balances.items():
        account = accounts.get(account_id)
        if account is None or account.is_group:
            continue
        if (account.account_type or "") != ACCOUNTING_DEPRECIATION_ACCOUNT_TYPE:
            continue
        total = q(total + money(debit) - money(credit))
    return total


def _income_line_from_books(net_profit: Decimal) -> TaxComputationIncomeLineIn:
    return TaxComputationIncomeLineIn(
        seq=0,
        head="PGBP",
        income_character_code="ORDINARY",
        sub_ref=BOOK_PROFIT_SUB_REF,
        gross=net_profit,
        deductions=ZERO,
        net=net_profit,
        source_doc_type=BOOK_PROFIT_SOURCE_DOC_TYPE,
    )


def _depreciation_addback_line(amount: Decimal) -> TaxComputationAdjustmentLineIn:
    return TaxComputationAdjustmentLineIn(
        provision_section_code=DEPRECIATION_ADDBACK_SECTION,
        section_code=DEPRECIATION_ADDBACK_SECTION,
        stage="PGBP",
        direction="Add",
        amount=amount,
        description=DEPRECIATION_ADDBACK_DESCRIPTION,
        status="Manual",
    )


def _existing_draft_adjustments(doc: TaxComputation) -> list[TaxComputationAdjustmentLineIn]:
    """Draft (unevaluated) adjustment rows, minus any previous book-depreciation add-back."""
    out: list[TaxComputationAdjustmentLineIn] = []
    for line in doc.adjustment_lines:
        if line.run_id is not None:
            continue
        if line.description == DEPRECIATION_ADDBACK_DESCRIPTION:
            continue
        out.append(
            TaxComputationAdjustmentLineIn(
                provision_section_code=line.provision_section_code,
                rule_code=line.rule_code,
                section_code=line.section_code,
                stage=line.stage,
                description=line.description,
                direction=line.direction,
                amount=line.amount,
                override_amount=line.override_amount,
                status=line.status,
            )
        )
    return out


async def populate_from_books(
    db: AsyncSession,
    *,
    computation_id: uuid.UUID,
    payload: TaxPopulateFromBooksIn,
    user: CurrentUser,
) -> TaxPopulateFromBooksOut:
    """Write the general-ledger profit (and book depreciation add-back) onto the draft."""
    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    doc = await computation_service.get_computation(db, user.company_id, computation_id)
    if doc.docstatus != 0:
        raise ValidationError(
            "Figures can only be pulled from the books while the computation is a draft",
            code="not_draft",
            field="docstatus",
        )

    fy_start, fy_end = await assessment_year_window(db, doc.ay_code)
    figures = await read_books(
        db, company_id=user.company_id, fy_start=fy_start, fy_end=fy_end
    )
    window = f"{fy_start.isoformat()} to {fy_end.isoformat()}"
    notes = [
        f"Net profit of {format(figures.net_profit, 'f')} read from the general ledger "
        f"for the financial year {window}."
    ]

    income_lines: list[TaxComputationIncomeLineIn] | None = None
    income_written = 0
    if payload.replace_income_heads:
        income_lines = [_income_line_from_books(figures.net_profit)]
        income_written = 1
        notes.append(
            "Income under Profits and Gains of Business or Profession replaced with the "
            "net profit as per books; enter the tax adjustments next."
        )

    adjustment_lines: list[TaxComputationAdjustmentLineIn] | None = None
    adjustments_written = 0
    if payload.add_accounting_depreciation_addback:
        if figures.accounting_depreciation != ZERO:
            adjustment_lines = _existing_draft_adjustments(doc)
            adjustment_lines.append(_depreciation_addback_line(figures.accounting_depreciation))
            adjustments_written = 1
            notes.append(
                f"Depreciation of {format(figures.accounting_depreciation, 'f')} charged in the "
                "books was added back; claim depreciation under the Income-tax Act separately."
            )
        else:
            notes.append(
                "No depreciation was charged in the books for this year, so nothing was added back."
            )

    book_profit = figures.net_profit if payload.set_book_profit else None
    if payload.set_book_profit:
        notes.append(
            "Book profit for the Minimum Alternate Tax comparison set to the net profit as per books."
        )

    updated = await computation_service.update_computation(
        db,
        computation_id,
        TaxComputationUpdate(
            book_profit_115jb=book_profit,
            income_lines=income_lines,
            adjustment_lines=adjustment_lines,
        ),
        user,
    )
    return TaxPopulateFromBooksOut(
        ay_code=doc.ay_code,
        fy_start=fy_start,
        fy_end=fy_end,
        book_profit=figures.net_profit,
        accounting_depreciation=figures.accounting_depreciation,
        income_lines_written=income_written,
        adjustment_lines_written=adjustments_written,
        notes=notes,
        computation=computation_service.computation_out(updated),
    )
