"""Financial report endpoints — Module 02 (Section 3, Module 02, rule 7)."""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.accounts import (
    AgingRow,
    BankReconciliationReport,
    BatchEmailResultRow,
    BudgetVarianceRow,
    CashFlowRow,
    CollectionSummaryRow,
    ContributionMarginReport,
    DunningNotice,
    EmailDunningBatchRequest,
    EmailDunningRequest,
    EmailStatementRequest,
    EmailStatementsBatchRequest,
    GrossProfitReport,
    PartyLedgerSummaryRow,
    PartyOutstandingSummaryRow,
    PeriodClosingCreate,
    PeriodClosingResponse,
    RegisterReport,
    ShareBalanceRow,
    ShareLedgerRow,
    StatementOfAccounts,
    TrialBalanceRow,
)
from app.schemas.printing import EmailSendResult
from app.services import financial_reports as reports
from app.services import dunning, period_closing, share_transfer, statements

router = APIRouter(prefix="/reports", tags=["accounts: reports"])


def _company(current_user: CurrentUser) -> uuid.UUID:
    if current_user.company_id is None:
        raise ValidationError("An active company is required")
    return current_user.company_id


@router.get(
    "/share-balance",
    response_model=list[ShareBalanceRow],
    summary="Share Balance (cap table)",
    description="Each shareholder's holding per share type, with % of that type's total "
    "issued — derived from submitted Share Transfers as of the given date.",
)
async def share_balance(
    current_user: Annotated[CurrentUser, Depends(require_permission("Share Transfer", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
    shareholder_id: uuid.UUID | None = None,
    share_type_id: uuid.UUID | None = None,
) -> list[ShareBalanceRow]:
    return await share_transfer.shareholder_balances(
        db, _company(current_user), as_of=as_of or date.today(),
        shareholder_id=shareholder_id, share_type_id=share_type_id,
    )


@router.get(
    "/share-ledger",
    response_model=list[ShareLedgerRow],
    summary="Share Ledger",
    description="Chronological list of submitted share transfers, optionally filtered by "
    "date window, shareholder or share type.",
)
async def share_ledger(
    current_user: Annotated[CurrentUser, Depends(require_permission("Share Transfer", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date | None = None,
    to_date: date | None = None,
    shareholder_id: uuid.UUID | None = None,
    share_type_id: uuid.UUID | None = None,
) -> list[ShareLedgerRow]:
    return await share_transfer.share_ledger(
        db, _company(current_user), from_date=from_date, to_date=to_date,
        shareholder_id=shareholder_id, share_type_id=share_type_id,
    )


@router.get(
    "/general-ledger",
    summary="General Ledger",
    description="Ledger entries in a date window with opening/closing balances; "
    "filter by account, party or voucher number.",
)
async def general_ledger(
    current_user: Annotated[CurrentUser, Depends(require_permission("GL Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
    account_id: uuid.UUID | None = None,
    party_type: str | None = None,
    party_id: uuid.UUID | None = None,
    voucher_no: str | None = None,
) -> dict:
    return await reports.general_ledger(
        db, _company(current_user),
        from_date=from_date, to_date=to_date,
        account_id=account_id, party_type=party_type, party_id=party_id, voucher_no=voucher_no,
    )


@router.get(
    "/trial-balance",
    response_model=list[TrialBalanceRow],
    summary="Trial Balance",
    description="Opening / period / closing debit-credit per account for a fiscal year, "
    "with group accounts rolled up.",
)
async def trial_balance(
    current_user: Annotated[CurrentUser, Depends(require_permission("GL Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    fiscal_year_id: uuid.UUID,
) -> list[TrialBalanceRow]:
    return await reports.trial_balance(db, _company(current_user), fiscal_year_id=fiscal_year_id)


@router.get(
    "/profit-loss",
    summary="Profit and Loss Statement",
    description="Income and expense balances for a period plus net profit.",
)
async def profit_loss(
    current_user: Annotated[CurrentUser, Depends(require_permission("GL Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> dict:
    return await reports.profit_and_loss(db, _company(current_user), from_date=from_date, to_date=to_date)


@router.get(
    "/balance-sheet",
    summary="Balance Sheet",
    description="Assets, liabilities and equity as of a date; includes the provisional "
    "(un-closed) profit/loss line that keeps the equation balanced.",
)
async def balance_sheet(
    current_user: Annotated[CurrentUser, Depends(require_permission("GL Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date,
) -> dict:
    return await reports.balance_sheet(db, _company(current_user), as_of=as_of)


@router.get(
    "/accounts-receivable",
    response_model=list[AgingRow],
    summary="Accounts Receivable (aging)",
    description="Outstanding sales invoices per customer with 0-30/31-60/61-90/90+ buckets.",
)
async def accounts_receivable(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
) -> list[AgingRow]:
    return await reports.accounts_receivable(db, _company(current_user), as_of=as_of or date.today())


@router.get(
    "/accounts-payable",
    response_model=list[AgingRow],
    summary="Accounts Payable (aging)",
    description="Outstanding purchase invoices per supplier with aging buckets.",
)
async def accounts_payable(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
) -> list[AgingRow]:
    return await reports.accounts_payable(db, _company(current_user), as_of=as_of or date.today())


@router.get(
    "/cash-flow",
    response_model=list[CashFlowRow],
    summary="Cash Flow Statement",
    description="Direct-method cash movement on Cash/Bank accounts for a period.",
)
async def cash_flow(
    current_user: Annotated[CurrentUser, Depends(require_permission("GL Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> list[CashFlowRow]:
    return await reports.cash_flow(db, _company(current_user), from_date=from_date, to_date=to_date)


@router.get(
    "/bank-reconciliation",
    response_model=BankReconciliationReport,
    summary="Bank Reconciliation Statement",
    description="Balance per books vs. per bank for a Bank/Cash GL account: lists "
    "submitted payments and journal entries not yet cleared by the bank "
    "(no clearance_date as of the date).",
)
async def bank_reconciliation(
    current_user: Annotated[CurrentUser, Depends(require_permission("Payment Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    gl_account_id: uuid.UUID,
    as_of: date | None = None,
) -> BankReconciliationReport:
    return await reports.bank_reconciliation(
        db, _company(current_user), gl_account_id=gl_account_id, as_of=as_of or date.today()
    )


@router.get(
    "/sales-register",
    response_model=RegisterReport,
    summary="Sales Register",
    description="All submitted sales invoices in a date window with net / tax / grand / "
    "outstanding totals — the basis for GST output returns.",
)
async def sales_register(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> RegisterReport:
    return await reports.sales_register(db, _company(current_user), from_date=from_date, to_date=to_date)


@router.get(
    "/purchase-register",
    response_model=RegisterReport,
    summary="Purchase Register",
    description="All submitted purchase invoices in a date window with net / tax / grand / "
    "outstanding totals — the basis for GST input-credit returns.",
)
async def purchase_register(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> RegisterReport:
    return await reports.purchase_register(db, _company(current_user), from_date=from_date, to_date=to_date)


@router.get(
    "/budget-variance",
    response_model=list[BudgetVarianceRow],
    summary="Budget Variance",
    description="Per budgeted account for a fiscal year: budgeted vs actual (GL) spend and the "
    "variance. Positive variance means under budget.",
)
async def budget_variance(
    current_user: Annotated[CurrentUser, Depends(require_permission("Budget", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    fiscal_year_id: uuid.UUID,
) -> list[BudgetVarianceRow]:
    return await reports.budget_variance(db, _company(current_user), fiscal_year_id=fiscal_year_id)


@router.get(
    "/gross-profit",
    response_model=GrossProfitReport,
    summary="Gross Profit (by item)",
    description="Per-item selling vs COGS margin for submitted sales invoices in a window. "
    "COGS uses each item's latest moving-average stock valuation; returns and opening "
    "invoices are excluded.",
)
async def gross_profit(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> GrossProfitReport:
    return await reports.gross_profit(db, _company(current_user), from_date=from_date, to_date=to_date)


@router.get(
    "/contribution-margin",
    response_model=ContributionMarginReport,
    summary="Contribution Margin (CM1 / CM2 / CM3)",
    description="Managerial P&L waterfall from Account.cm_class tags. Optional cost_center_id "
    "filters GL postings. Statutory Profit & Loss is unchanged.",
)
async def contribution_margin_report(
    current_user: Annotated[CurrentUser, Depends(require_permission("GL Entry", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
    cost_center_id: uuid.UUID | None = None,
) -> ContributionMarginReport:
    return await reports.contribution_margin(
        db,
        _company(current_user),
        from_date=from_date,
        to_date=to_date,
        cost_center_id=cost_center_id,
    )


@router.get(
    "/customer-ledger-summary",
    response_model=list[PartyLedgerSummaryRow],
    summary="Customer Ledger Summary",
    description="Per-customer opening / period debit-credit / closing from the receivable "
    "ledger — the account-statement backbone.",
)
async def customer_ledger_summary(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> list[PartyLedgerSummaryRow]:
    return await reports.customer_ledger_summary(
        db, _company(current_user), from_date=from_date, to_date=to_date
    )


@router.get(
    "/supplier-ledger-summary",
    response_model=list[PartyLedgerSummaryRow],
    summary="Supplier Ledger Summary",
    description="Per-supplier opening / period debit-credit / closing from the payable ledger.",
)
async def supplier_ledger_summary(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> list[PartyLedgerSummaryRow]:
    return await reports.supplier_ledger_summary(
        db, _company(current_user), from_date=from_date, to_date=to_date
    )


@router.post(
    "/period-closing",
    response_model=PeriodClosingResponse,
    status_code=201,
    summary="Run a Period Closing Voucher",
    description="Transfers P&L balances to the closing (retained earnings) account and "
    "freezes GL postings up to the date. Atomic create + submit.",
)
async def run_period_closing(
    payload: PeriodClosingCreate,
    current_user: Annotated[
        CurrentUser, Depends(require_permission("Period Closing Voucher", "submit"))
    ],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PeriodClosingResponse:
    return PeriodClosingResponse.model_validate(
        await period_closing.create_and_submit_period_closing(db, payload, current_user)
    )


# --- AR/AP summary + collection period -------------------------------------------------------


@router.get(
    "/accounts-receivable-summary",
    summary="AR Summary (per customer)",
    description="Total receivable per customer, rolled up across their open invoices, with aging buckets.",
)
async def accounts_receivable_summary(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
) -> list[PartyOutstandingSummaryRow]:
    return await reports.accounts_receivable_summary(db, _company(current_user), as_of=as_of or date.today())


@router.get(
    "/accounts-payable-summary",
    summary="AP Summary (per supplier)",
    description="Total payable per supplier, rolled up across their open bills, with aging buckets.",
)
async def accounts_payable_summary(
    current_user: Annotated[CurrentUser, Depends(require_permission("Purchase Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date | None = None,
) -> list[PartyOutstandingSummaryRow]:
    return await reports.accounts_payable_summary(db, _company(current_user), as_of=as_of or date.today())


@router.get(
    "/collection-summary",
    summary="Collection period (per customer)",
    description="For sales invoices fully paid in the window, the average days from invoice to "
    "payment per customer — i.e. how fast each customer actually pays.",
)
async def collection_summary(
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> list[CollectionSummaryRow]:
    return await reports.collection_summary(
        db, _company(current_user), from_date=from_date, to_date=to_date
    )


# --- Statement of Accounts -------------------------------------------------------------------


@router.get(
    "/statement-of-accounts/{customer_id}",
    summary="Customer Statement of Accounts",
    description="Per-customer ledger statement for a date window: opening balance, each "
    "voucher with a running balance, closing balance, and aging of the amount still due.",
)
async def statement_of_accounts(
    customer_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
) -> StatementOfAccounts:
    return await statements.build_statement(
        db, _company(current_user), customer_id,
        party_type="Customer", from_date=from_date, to_date=to_date,
    )


@router.get(
    "/statement-of-accounts/{customer_id}/print",
    summary="Customer Statement of Accounts — PDF/HTML",
    description="Renders the statement as a themed PDF (`?format=pdf`, default) or HTML preview "
    "(`?format=html`). Returns 501 if the PDF engine is not installed.",
)
async def statement_of_accounts_print(
    customer_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    from_date: date,
    to_date: date,
    format: Annotated[Literal["pdf", "html"], Query()] = "pdf",
) -> Response:
    content, filename, media_type = await statements.render_statement(
        db, _company(current_user), customer_id,
        party_type="Customer", from_date=from_date, to_date=to_date, fmt=format,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post(
    "/statement-of-accounts/email",
    summary="Email a customer their statement of accounts",
    description="Renders the statement to PDF and emails it (recipient defaults to the "
    "customer's saved email). Requires `email` permission. Never raises on a delivery "
    "failure — returns status `Failed` with the error.",
)
async def email_statement(
    payload: EmailStatementRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "email"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EmailSendResult:
    log = await statements.email_statement(
        db, _company(current_user), payload.customer_id, current_user.id,
        party_type="Customer", from_date=payload.from_date, to_date=payload.to_date,
        to=[str(x) for x in payload.to] if payload.to else None,
        subject=payload.subject, body=payload.body,
    )
    await db.commit()
    return EmailSendResult(
        status=log.status, to=log.to_addresses, email_log_id=log.id, error=log.error_message,
    )


@router.post(
    "/statement-of-accounts/email-batch",
    summary="Email statements to many customers",
    description="Bulk-emails statements. `customer_ids` omitted → every customer with an "
    "outstanding balance as of `to_date`. Customers without an email are skipped. "
    "Requires `email` permission.",
)
async def email_statements_batch(
    payload: EmailStatementsBatchRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "email"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[BatchEmailResultRow]:
    return await statements.email_statements_batch(
        db, _company(current_user), current_user.id,
        party_type="Customer", from_date=payload.from_date, to_date=payload.to_date,
        party_ids=payload.customer_ids, subject=payload.subject, body=payload.body,
    )


# --- Dunning (overdue reminders) -------------------------------------------------------------


@router.get(
    "/dunning/{customer_id}",
    summary="Overdue reminder (dunning) for a customer",
    description="The customer's overdue invoices as of a date, with per-invoice interest, an "
    "auto-selected escalation tier (Dunning Type), a flat fee, and the total now due.",
)
async def dunning_notice(
    customer_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date,
) -> DunningNotice:
    return await dunning.build_dunning(db, _company(current_user), customer_id, as_of=as_of)


@router.get(
    "/dunning/{customer_id}/print",
    summary="Dunning reminder — PDF/HTML",
    description="Renders the reminder letter as a themed PDF (`?format=pdf`, default) or HTML.",
)
async def dunning_print(
    customer_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "report"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    as_of: date,
    format: Annotated[Literal["pdf", "html"], Query()] = "pdf",
) -> Response:
    content, filename, media_type = await dunning.render_dunning(
        db, _company(current_user), customer_id, as_of=as_of, fmt=format
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post(
    "/dunning/email",
    summary="Email an overdue reminder to a customer",
    description="Renders the reminder to PDF and emails it (recipient defaults to the customer's "
    "saved email). Requires `email` permission. 422 if the customer has no overdue invoices.",
)
async def email_dunning(
    payload: EmailDunningRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "email"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> EmailSendResult:
    log = await dunning.email_dunning(
        db, _company(current_user), payload.customer_id, current_user.id,
        as_of=payload.as_of,
        to=[str(x) for x in payload.to] if payload.to else None,
        subject=payload.subject, body=payload.body,
    )
    await db.commit()
    return EmailSendResult(
        status=log.status, to=log.to_addresses, email_log_id=log.id, error=log.error_message,
    )


@router.post(
    "/dunning/email-batch",
    summary="Email reminders to all overdue customers",
    description="`customer_ids` omitted → every customer with an overdue invoice as of `as_of`. "
    "Customers without overdue invoices or without an email are skipped. Requires `email` permission.",
)
async def email_dunning_batch(
    payload: EmailDunningBatchRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission("Sales Invoice", "email"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[BatchEmailResultRow]:
    return await dunning.email_dunning_batch(
        db, _company(current_user), current_user.id,
        as_of=payload.as_of, customer_ids=payload.customer_ids,
        subject=payload.subject, body=payload.body,
    )
