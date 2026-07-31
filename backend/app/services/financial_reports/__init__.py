"""Financial reports — Module 02. Split into sub-modules (ledger, statements,
receivables, registers, analysis) over a shared _helpers; all public report
functions are re-exported so `from app.services import financial_reports as reports`
and `reports.general_ledger(...)` keep working."""

from app.services.financial_reports.ledger import general_ledger
from app.services.financial_reports.ledger import trial_balance
from app.services.financial_reports.statements import profit_and_loss
from app.services.financial_reports.statements import balance_sheet
from app.services.financial_reports.statements import cash_flow
from app.services.financial_reports.receivables import accounts_receivable
from app.services.financial_reports.receivables import accounts_payable
from app.services.financial_reports.receivables import accounts_receivable_summary
from app.services.financial_reports.receivables import accounts_payable_summary
from app.services.financial_reports.receivables import collection_summary
from app.services.financial_reports.receivables import bank_reconciliation
from app.services.financial_reports.registers import sales_register
from app.services.financial_reports.registers import purchase_register
from app.services.financial_reports.registers import customer_ledger_summary
from app.services.financial_reports.registers import supplier_ledger_summary
from app.services.financial_reports.analysis import gross_profit
from app.services.financial_reports.analysis import budget_variance
from app.services.financial_reports.contribution_margin import contribution_margin

__all__ = [
    "general_ledger",
    "trial_balance",
    "profit_and_loss",
    "balance_sheet",
    "cash_flow",
    "accounts_receivable",
    "accounts_payable",
    "accounts_receivable_summary",
    "accounts_payable_summary",
    "collection_summary",
    "bank_reconciliation",
    "sales_register",
    "purchase_register",
    "customer_ledger_summary",
    "supplier_ledger_summary",
    "gross_profit",
    "budget_variance",
    "contribution_margin",
]
