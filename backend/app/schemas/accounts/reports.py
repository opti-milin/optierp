"""Accounts schemas — financial reports (GL, trial balance, statements, registers, gross profit, budget variance)."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, EmailStr

from app.schemas.common import ORMModel

class GLEntryResponse(ORMModel):
    id: uuid.UUID
    posting_date: date
    account_id: uuid.UUID
    party_type: str | None
    party_id: uuid.UUID | None
    debit: Decimal
    credit: Decimal
    voucher_type: str
    voucher_no: str
    against: str | None
    is_cancellation: bool
    remarks: str | None


class TrialBalanceRow(BaseModel):
    account_id: uuid.UUID
    account_name: str
    root_type: str
    is_group: bool
    path: str
    opening_debit: Decimal
    opening_credit: Decimal
    debit: Decimal
    credit: Decimal
    closing_debit: Decimal
    closing_credit: Decimal


class FinancialStatementRow(BaseModel):
    account_id: uuid.UUID | None
    account_name: str
    root_type: str | None
    is_group: bool
    indent: int
    amount: Decimal


class AgingRow(BaseModel):
    party_id: uuid.UUID
    party_name: str
    voucher_no: str
    voucher_id: uuid.UUID
    posting_date: date
    due_date: date | None
    grand_total: Decimal
    outstanding_amount: Decimal
    age_days: int
    bucket_0_30: Decimal
    bucket_31_60: Decimal
    bucket_61_90: Decimal
    bucket_90_plus: Decimal


class CashFlowRow(BaseModel):
    section: str
    label: str
    amount: Decimal


class PartyOutstandingSummaryRow(BaseModel):
    """One row per party: their total outstanding rolled up across all open invoices, aged."""

    party_id: uuid.UUID
    party_name: str
    outstanding_amount: Decimal
    bucket_0_30: Decimal
    bucket_31_60: Decimal
    bucket_61_90: Decimal
    bucket_90_plus: Decimal


class CollectionSummaryRow(BaseModel):
    """Per-customer collection behaviour over a period: how fast they actually pay."""

    party_id: uuid.UUID
    party_name: str
    paid_invoices: int
    avg_days_to_pay: int
    total_collected: Decimal


# --- Registers & party ledger summaries ------------------------------------------------------


class RegisterRow(BaseModel):
    voucher_id: uuid.UUID
    name: str
    posting_date: date
    party_name: str | None
    net_total: Decimal
    total_taxes_and_charges: Decimal
    grand_total: Decimal
    outstanding_amount: Decimal
    status: str


class RegisterReport(BaseModel):
    rows: list[RegisterRow]
    total_net: Decimal
    total_tax: Decimal
    total_grand: Decimal
    total_outstanding: Decimal


class PartyLedgerSummaryRow(BaseModel):
    party_id: uuid.UUID
    party_name: str
    opening: Decimal
    debit: Decimal
    credit: Decimal
    closing: Decimal


class StatementLine(BaseModel):
    posting_date: date
    voucher_type: str
    voucher_no: str
    voucher_id: uuid.UUID | None
    remarks: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal  # running balance after this line


class StatementOfAccounts(BaseModel):
    party_type: str  # Customer | Supplier
    party_id: uuid.UUID
    party_name: str
    party_email: str | None
    from_date: date
    to_date: date
    opening_balance: Decimal
    lines: list[StatementLine]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    aging_0_30: Decimal
    aging_31_60: Decimal
    aging_61_90: Decimal
    aging_90_plus: Decimal
    aging_total: Decimal


class EmailStatementRequest(BaseModel):
    customer_id: uuid.UUID
    from_date: date
    to_date: date
    to: list[EmailStr] | None = None  # defaults to the customer's saved email
    subject: str | None = None
    body: str | None = None


class EmailStatementsBatchRequest(BaseModel):
    from_date: date
    to_date: date
    customer_ids: list[uuid.UUID] | None = None  # None → all customers with an outstanding balance
    subject: str | None = None
    body: str | None = None


class BatchEmailResultRow(BaseModel):
    party_id: uuid.UUID
    party_name: str
    status: str  # Sent | Failed | Skipped
    detail: str | None = None


class DunningInvoiceRow(BaseModel):
    voucher_id: uuid.UUID
    voucher_no: str
    posting_date: date
    due_date: date | None
    age_days: int
    outstanding_amount: Decimal
    interest: Decimal


class DunningNotice(BaseModel):
    party_id: uuid.UUID
    party_name: str
    party_email: str | None
    as_of: date
    dunning_type: str | None  # selected tier name, or None if no tier configured/applies
    letter_intro: str | None
    invoices: list[DunningInvoiceRow]
    total_overdue: Decimal
    total_interest: Decimal
    dunning_fee: Decimal
    total_due: Decimal


class EmailDunningRequest(BaseModel):
    customer_id: uuid.UUID
    as_of: date
    to: list[EmailStr] | None = None
    subject: str | None = None
    body: str | None = None


class EmailDunningBatchRequest(BaseModel):
    as_of: date
    customer_ids: list[uuid.UUID] | None = None  # None → all customers with overdue invoices
    subject: str | None = None
    body: str | None = None


class GrossProfitRow(BaseModel):
    item_code: str | None
    item_name: str
    qty: Decimal
    selling: Decimal
    cogs: Decimal
    gross_profit: Decimal
    margin_pct: Decimal


class GrossProfitReport(BaseModel):
    rows: list[GrossProfitRow]
    total_selling: Decimal
    total_cogs: Decimal
    total_gross_profit: Decimal
    margin_pct: Decimal


class BudgetVarianceRow(BaseModel):
    account_id: uuid.UUID
    account_name: str
    budget: Decimal
    actual: Decimal
    variance: Decimal
    variance_pct: Decimal


class ContributionMarginSection(BaseModel):
    cm_class: str | None  # None for unclassified bucket
    label: str
    rows: list[FinancialStatementRow]
    total: Decimal
    pct_of_revenue: Decimal | None = None


class ContributionMarginReport(BaseModel):
    from_date: date
    to_date: date
    cost_center_id: uuid.UUID | None = None
    sections: list[ContributionMarginSection]
    revenue: Decimal
    variable_cost: Decimal
    product_channel_fixed: Decimal
    segment_bu_fixed: Decimal
    corporate_overhead: Decimal
    cm1: Decimal
    cm2: Decimal
    cm3: Decimal
    operating_profit: Decimal
    cm1_pct: Decimal | None = None
    cm2_pct: Decimal | None = None
    cm3_pct: Decimal | None = None
    unclassified_total: Decimal
    warnings: list[str] = []


class CmTemplateInfo(BaseModel):
    id: str
    label: str
    description: str | None = None
    rule_count: int


class CmApplyTemplateRequest(BaseModel):
    template_id: str
    overwrite: bool = False


class CmApplyTemplateResult(BaseModel):
    template_id: str
    matched: int
    updated: int
    skipped: int


class CmUnclassifiedAccount(BaseModel):
    account_id: uuid.UUID
    account_name: str
    root_type: str
    account_type: str | None
    account_category: str | None = None
    path: str


class CmSettings(BaseModel):
    unclassified_policy: str = "bucket"  # bucket | exclude | error
    show_zero_rows: bool = False
    applied_template: str | None = None


# --- Budget ----------------------------------------------------------------------------------


