"""Master v1 router — module routers register here as they are migrated."""

from fastapi import APIRouter

from app.api.v1 import auth
from app.api.v1.assets import assets as assets_module, reports as assets_reports
from app.api.v1.compliance import (
    e_documents as compliance_e_documents,
    gst_settings as compliance_gst_settings,
    hsn_codes as compliance_hsn_codes,
    returns as compliance_returns,
    taxation_workspace as compliance_taxation_workspace,
    tds_returns as compliance_tds_returns,
)
from app.api.v1.accounts import (
    bank_reconciliation,
    budgets,
    contribution_margin as accounts_cm,
    journal_entries,
    masters as accounts_masters,
    payment_entries,
    payment_reconciliation,
    payment_requests,
    purchase_invoices,
    reports as accounts_reports,
    sales_invoices,
    share_transfers,
    subscriptions,
    workspace as accounts_workspace,
)
from app.api.v1.buying import purchase_orders, rfqs, workspace as buying_workspace
from app.api.v1.core import (
    companies,
    currencies,
    naming_series,
    ocr,
    print_docs,
    print_settings,
    roles,
    system_settings,
    uoms,
    users,
    workflows,
)
from app.api.v1.manufacturing import (
    boms as manufacturing_boms,
    job_cards as manufacturing_job_cards,
    production_plans as manufacturing_production_plans,
    quality_inspections as manufacturing_quality,
    reports as manufacturing_reports,
    subcontract_jobs as manufacturing_subcontract,
    work_orders as manufacturing_work_orders,
    workspace as manufacturing_workspace,
)
from app.api.v1 import registry as metadata_engine
from app.api.v1 import portal as secretarial_portal
from app.api.v1.secretarial import (
    capital as secretarial_capital,
    circulars as secretarial_circulars,
    compliance as secretarial_compliance,
    documents as secretarial_documents,
    filings as secretarial_filings,
    meetings as secretarial_meetings,
    engagements as secretarial_engagements,
    entities as secretarial_entities,
    files as secretarial_files,
    persons as secretarial_persons,
    registers as secretarial_registers,
    workspace as secretarial_workspace,
)
from app.api.v1.selling import cm_plans, quotations, sales_orders, workspace as selling_workspace
from app.api.v1.migration import (
    catalogue as migration_catalogue,
    imports as migration_imports,
    mappings as migration_mappings,
    sources as migration_sources,
)
from app.api.v1.tax import (
    calendar as tax_calendar,
    catalogue as tax_catalogue,
    challans as tax_challans,
    computations as tax_computations,
    credits as tax_credits,
    depreciation as tax_depreciation,
    filings as tax_filings,
    losses as tax_losses,
    mat_credits as tax_mat_credits,
    registrations as tax_registrations,
    workspace as tax_workspace,
)
from app.api.v1.stock import (
    delivery_notes,
    masters as stock_masters,
    material_requests,
    purchase_receipts,
    reorder as stock_reorder,
    reports as stock_reports,
    serials as stock_serials,
    service_credits,
    stock_entries,
    stock_reconciliations,
    workspace as stock_workspace,
)

api_v1_router = APIRouter()

# Module 01 — Core / Setup
api_v1_router.include_router(auth.router)
api_v1_router.include_router(companies.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(roles.router)
api_v1_router.include_router(currencies.router)
api_v1_router.include_router(uoms.router)
api_v1_router.include_router(naming_series.router)
api_v1_router.include_router(workflows.router)
api_v1_router.include_router(system_settings.router)
api_v1_router.include_router(print_settings.router)
api_v1_router.include_router(print_docs.router)
api_v1_router.include_router(ocr.router)

# Module 02 — Accounts
api_v1_router.include_router(accounts_masters.router)
api_v1_router.include_router(journal_entries.router)
api_v1_router.include_router(sales_invoices.router)
api_v1_router.include_router(purchase_invoices.router)
api_v1_router.include_router(payment_entries.router)
api_v1_router.include_router(payment_reconciliation.router)
api_v1_router.include_router(payment_requests.router)
api_v1_router.include_router(subscriptions.router)
api_v1_router.include_router(share_transfers.router)
api_v1_router.include_router(accounts_cm.router)
api_v1_router.include_router(bank_reconciliation.router)
api_v1_router.include_router(budgets.router)
api_v1_router.include_router(accounts_reports.router)
api_v1_router.include_router(accounts_workspace.router)

# Module 03 — Stock
api_v1_router.include_router(stock_masters.router)
api_v1_router.include_router(stock_entries.router)
api_v1_router.include_router(stock_reconciliations.router)
api_v1_router.include_router(material_requests.router)
api_v1_router.include_router(purchase_receipts.router)
api_v1_router.include_router(delivery_notes.router)
api_v1_router.include_router(stock_reports.router)
api_v1_router.include_router(stock_reorder.router)
api_v1_router.include_router(stock_serials.router)
api_v1_router.include_router(service_credits.router)
api_v1_router.include_router(stock_workspace.router)

# Module 04 — Buying
api_v1_router.include_router(purchase_orders.router)
api_v1_router.include_router(rfqs.router)
api_v1_router.include_router(buying_workspace.router)

# Module 05 — Selling
api_v1_router.include_router(quotations.router)
api_v1_router.include_router(sales_orders.router)
api_v1_router.include_router(cm_plans.router)
api_v1_router.include_router(selling_workspace.router)

# Module — Assets (fixed-asset register + depreciation)
api_v1_router.include_router(assets_module.router)
api_v1_router.include_router(assets_reports.router)

# Module — Manufacturing (BOM → Work Order → Manufacture)
api_v1_router.include_router(manufacturing_boms.router)
api_v1_router.include_router(manufacturing_work_orders.router)
api_v1_router.include_router(manufacturing_job_cards.router)
api_v1_router.include_router(manufacturing_production_plans.router)
api_v1_router.include_router(manufacturing_subcontract.router)
api_v1_router.include_router(manufacturing_quality.router)
api_v1_router.include_router(manufacturing_reports.router)
api_v1_router.include_router(manufacturing_workspace.router)

# Module — India Compliance (GST settings, returns, e-documents)
api_v1_router.include_router(compliance_gst_settings.router)
api_v1_router.include_router(compliance_hsn_codes.router)
api_v1_router.include_router(compliance_returns.router)
api_v1_router.include_router(compliance_e_documents.router)
api_v1_router.include_router(compliance_tds_returns.router)
api_v1_router.include_router(compliance_taxation_workspace.router)

# Module — Income Tax (assessment-year computation workspace + supporting registers).
# `registrations` last: its prefix is the bare "/tax", so the more specific
# sub-prefixes above must match first.
api_v1_router.include_router(tax_workspace.router)
api_v1_router.include_router(tax_computations.router)
api_v1_router.include_router(tax_challans.router)
api_v1_router.include_router(tax_credits.router)
api_v1_router.include_router(tax_depreciation.router)
api_v1_router.include_router(tax_losses.router)
api_v1_router.include_router(tax_mat_credits.router)
api_v1_router.include_router(tax_calendar.router)
api_v1_router.include_router(tax_filings.router)
api_v1_router.include_router(tax_catalogue.router)
api_v1_router.include_router(tax_registrations.router)

# Module 12 — Data Migration (Tally XML/CSV + spreadsheets). `catalogue` last: its prefix is
# the bare "/migration", so the more specific sub-prefixes above must match first.
api_v1_router.include_router(migration_imports.router)
api_v1_router.include_router(migration_mappings.router)
api_v1_router.include_router(migration_sources.router)
api_v1_router.include_router(migration_sources.template_router)
api_v1_router.include_router(migration_catalogue.router)

# Module 13 — Company Secretarial & Governance. `entities` last among the bare
# "/secretarial" routers: its /entities/{id} path would otherwise shadow the more
# specific /secretarial/registers/... and /secretarial/compliance/... prefixes.
api_v1_router.include_router(secretarial_capital.router)
api_v1_router.include_router(secretarial_registers.router)
api_v1_router.include_router(secretarial_documents.router)
api_v1_router.include_router(secretarial_meetings.router)
api_v1_router.include_router(secretarial_circulars.router)
api_v1_router.include_router(secretarial_filings.router)
# Unauthenticated by design: directors hold capability tokens, not accounts.
api_v1_router.include_router(secretarial_portal.router)
api_v1_router.include_router(secretarial_compliance.router)
api_v1_router.include_router(secretarial_files.router)
api_v1_router.include_router(secretarial_engagements.router)
api_v1_router.include_router(secretarial_persons.router)
api_v1_router.include_router(secretarial_workspace.router)
api_v1_router.include_router(secretarial_entities.router)

# Metadata engine ("the machine") — generic CRUD/list/form for every registered
# DocType (app.registry). Adding a master needs no new router here.
api_v1_router.include_router(metadata_engine.router)

# Module 06+ — registered as each module is migrated
