// Lazy-loaded routes per module with an auth guard.

import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

/**
 * Old standalone Income Tax record screens → the workspace section that replaced them.
 * Kept as redirects so existing bookmarks and links keep working.
 */
const TAX_SECTION_REDIRECTS: RouteRecordRaw[] = (
  [
    ["computations", "overview"],
    ["challans", "challans"],
    ["credits", "credits"],
    ["depreciation", "depreciation"],
    ["losses", "losses"],
    ["mat-credits", "mat"],
    ["calendar", "interest"],
    ["filings", "filing"],
  ] as const
).map(([path, section]) => ({
  path: `tax/${path}`,
  name: `tax-${path}`,
  redirect: () => ({ name: "tax-workspace", query: { section } }),
}));

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/auth/LoginView.vue"),
    meta: { public: true },
  },
  // The director portal (Module 13). Public by design: the people opening these links
  // hold a capability token and no account, so they must never meet the login screen.
  // Outside the AppShell too — there is no tenant context and nothing to navigate to.
  {
    path: "/p/c/:token",
    name: "portal-circulation",
    component: () => import("@/views/portal/CirculationView.vue"),
    meta: { public: true },
  },
  {
    path: "/p/r/:token",
    name: "portal-consent",
    component: () => import("@/views/portal/ConsentView.vue"),
    meta: { public: true },
  },
  {
    path: "/",
    component: () => import("@/layouts/AppShell.vue"),
    children: [
      { path: "", name: "dashboard", component: () => import("@/views/dashboard/LauncherView.vue") },
      // Module 01 — Core / Setup
      {
        path: "companies",
        name: "companies",
        component: () => import("@/views/core/CompanyListView.vue"),
      },
      {
        path: "companies/new",
        name: "company-new",
        component: () => import("@/views/core/CompanyFormView.vue"),
      },
      {
        path: "companies/:id",
        name: "company-detail",
        component: () => import("@/views/core/CompanyFormView.vue"),
        props: true,
      },
      { path: "users", name: "users", component: () => import("@/views/core/UserListView.vue") },
      { path: "users/new", name: "user-new", component: () => import("@/views/core/UserFormView.vue") },
      { path: "roles", name: "roles", component: () => import("@/views/core/RoleListView.vue") },
      {
        path: "settings",
        name: "settings",
        component: () => import("@/views/core/SettingsView.vue"),
      },
      {
        path: "settings/print",
        name: "print-settings",
        component: () => import("@/views/core/PrintSettingsView.vue"),
      },
      {
        path: "gst-settings",
        name: "gst-settings",
        component: () => import("@/views/compliance/GstSettingsView.vue"),
      },
      {
        path: "gst-returns",
        name: "gst-returns",
        component: () => import("@/views/compliance/GstReturnsView.vue"),
      },
      {
        path: "tds-returns",
        name: "tds-returns",
        component: () => import("@/views/compliance/TdsReturnsView.vue"),
      },
      {
        path: "income-tax-settings",
        name: "income-tax-settings",
        component: () => import("@/views/compliance/IncomeTaxSettingsView.vue"),
      },
      {
        path: "tax",
        name: "income-tax-home",
        component: () => import("@/views/compliance/IncomeTaxHomeView.vue"),
      },
      {
        path: "tax/workspace/:id?",
        name: "tax-workspace",
        component: () => import("@/views/compliance/TaxWorkspaceView.vue"),
      },
      {
        path: "tax/catalogue",
        name: "tax-catalogue",
        component: () => import("@/views/compliance/StatutoryCatalogueView.vue"),
      },
      // The standalone record screens are now sections of the workspace. Their old
      // paths still resolve so that no bookmark breaks: without a computation in the
      // URL they land on the year list, which opens the right section from there.
      ...TAX_SECTION_REDIRECTS,
      {
        path: "income-tax/new",
        redirect: { name: "tax-workspace" },
      },
      {
        path: "income-tax/:id",
        redirect: (to) => ({ name: "tax-workspace", params: { id: String(to.params.id) } }),
      },
      {
        path: "income-tax",
        redirect: { name: "income-tax-home" },
      },
      // Module 02 — Accounts
      {
        path: "sales-invoices",
        name: "sales-invoices",
        component: () => import("@/views/accounts/InvoiceListView.vue"),
        props: { kind: "sales" },
      },
      {
        path: "sales-invoices/new",
        name: "sales-invoice-new",
        component: () => import("@/views/accounts/InvoiceFormView.vue"),
        props: { kind: "sales" },
      },
      {
        path: "sales-invoices/:id",
        name: "sales-invoice-detail",
        component: () => import("@/views/accounts/InvoiceFormView.vue"),
        props: (route) => ({ kind: "sales", id: route.params.id as string }),
      },
      {
        path: "purchase-invoices",
        name: "purchase-invoices",
        component: () => import("@/views/accounts/InvoiceListView.vue"),
        props: { kind: "purchase" },
      },
      {
        path: "purchase-invoices/new",
        name: "purchase-invoice-new",
        component: () => import("@/views/accounts/InvoiceFormView.vue"),
        props: { kind: "purchase" },
      },
      {
        path: "purchase-invoices/:id",
        name: "purchase-invoice-detail",
        component: () => import("@/views/accounts/InvoiceFormView.vue"),
        props: (route) => ({ kind: "purchase", id: route.params.id as string }),
      },
      {
        path: "journal-entries",
        name: "journal-entries",
        component: () => import("@/views/accounts/JournalEntryView.vue"),
      },
      {
        path: "journal-entries/:id",
        name: "journal-entry-detail",
        component: () => import("@/views/accounts/JournalEntryDetailView.vue"),
        props: true,
      },
      {
        path: "payment-entries",
        name: "payment-entries",
        component: () => import("@/views/accounts/PaymentEntryView.vue"),
      },
      {
        path: "payment-entries/:id",
        name: "payment-entry-detail",
        component: () => import("@/views/accounts/PaymentEntryDetailView.vue"),
        props: true,
      },
      {
        path: "payment-reconciliation",
        name: "payment-reconciliation",
        component: () => import("@/views/accounts/PaymentReconciliationView.vue"),
      },
      {
        path: "statements",
        name: "statements",
        component: () => import("@/views/accounts/StatementOfAccountsView.vue"),
      },
      {
        path: "dunning",
        name: "dunning",
        component: () => import("@/views/accounts/DunningView.vue"),
      },
      {
        path: "payment-requests",
        name: "payment-requests-list",
        component: () => import("@/views/accounts/PaymentRequestView.vue"),
      },
      {
        path: "subscriptions",
        name: "subscriptions",
        component: () => import("@/views/accounts/SubscriptionView.vue"),
      },
      {
        path: "share-transfers",
        name: "share-transfers",
        component: () => import("@/views/accounts/ShareTransferView.vue"),
      },
      {
        path: "budgets",
        name: "budgets",
        component: () => import("@/views/accounts/BudgetView.vue"),
      },
      {
        path: "chart-of-accounts",
        name: "chart-of-accounts",
        component: () => import("@/views/accounts/ChartOfAccountsView.vue"),
      },
      {
        path: "tax-templates",
        name: "tax-templates",
        component: () => import("@/views/accounts/TaxTemplateView.vue"),
      },
      {
        path: "opening-invoices",
        name: "opening-invoices",
        component: () => import("@/views/accounts/OpeningInvoicesView.vue"),
      },
      {
        path: "bank-reconciliation",
        name: "bank-reconciliation",
        component: () => import("@/views/accounts/BankReconciliationView.vue"),
      },
      {
        path: "reports",
        name: "reports",
        component: () => import("@/views/accounts/ReportsView.vue"),
      },
      // Module 03 — Stock
      { path: "items", name: "items", component: () => import("@/views/stock/ItemsView.vue") },
      { path: "items/new", name: "item-new", component: () => import("@/views/stock/ItemFormView.vue") },
      {
        path: "items/:id",
        name: "item-detail",
        component: () => import("@/views/stock/ItemFormView.vue"),
        props: true,
      },
      {
        path: "warehouses",
        name: "warehouses",
        component: () => import("@/views/stock/WarehousesView.vue"),
      },
      { path: "warehouses/new", name: "warehouse-new", component: () => import("@/views/stock/WarehouseFormView.vue") },
      {
        path: "warehouses/:id",
        name: "warehouse-detail",
        component: () => import("@/views/stock/WarehouseFormView.vue"),
        props: true,
      },
      {
        path: "stock-entries",
        name: "stock-entries",
        component: () => import("@/views/stock/StockEntryView.vue"),
      },
      { path: "stock-entries/new", name: "stock-entry-new", component: () => import("@/views/stock/StockEntryFormView.vue") },
      {
        path: "stock-entries/:id",
        name: "stock-entry-detail",
        component: () => import("@/views/stock/StockEntryFormView.vue"),
        props: true,
      },
      {
        path: "stock-reconciliations",
        name: "stock-reconciliations",
        component: () => import("@/views/stock/StockReconciliationView.vue"),
      },
      { path: "stock-reconciliations/new", name: "stock-reconciliation-new", component: () => import("@/views/stock/StockReconciliationFormView.vue") },
      {
        path: "stock-reconciliations/:id",
        name: "stock-reconciliation-detail",
        component: () => import("@/views/stock/StockReconciliationFormView.vue"),
        props: true,
      },
      {
        path: "reorder",
        name: "reorder",
        component: () => import("@/views/stock/ReorderView.vue"),
      },
      {
        path: "service-credits",
        name: "service-credits",
        component: () => import("@/views/stock/ServiceCreditView.vue"),
      },
      { path: "service-credits/new", name: "service-credit-new", component: () => import("@/views/stock/ServiceCreditFormView.vue") },
      {
        path: "service-credits/:id",
        name: "service-credit-detail",
        component: () => import("@/views/stock/ServiceCreditFormView.vue"),
        props: true,
      },
      {
        path: "material-requests",
        name: "material-requests",
        component: () => import("@/views/stock/MaterialRequestView.vue"),
      },
      { path: "material-requests/new", name: "material-request-new", component: () => import("@/views/stock/MaterialRequestFormView.vue") },
      {
        path: "material-requests/:id",
        name: "material-request-detail",
        component: () => import("@/views/stock/MaterialRequestFormView.vue"),
        props: true,
      },
      {
        path: "stock-balance",
        name: "stock-balance",
        component: () => import("@/views/stock/StockBalanceView.vue"),
      },
      {
        path: "serial-nos",
        name: "serial-nos",
        component: () => import("@/views/stock/SerialNoView.vue"),
      },
      {
        path: "purchase-receipts",
        name: "purchase-receipts",
        component: () => import("@/views/trade/FulfilmentView.vue"),
        props: { kind: "purchase-receipt" },
      },
      {
        path: "purchase-receipts/new",
        name: "purchase-receipt-new",
        component: () => import("@/views/trade/PurchaseReceiptFormView.vue"),
      },
      {
        path: "purchase-receipts/:id",
        name: "purchase-receipt-detail",
        component: () => import("@/views/trade/FulfilmentView.vue"),
        props: (route) => ({ kind: "purchase-receipt", id: route.params.id as string }),
      },
      {
        path: "delivery-notes",
        name: "delivery-notes",
        component: () => import("@/views/trade/FulfilmentView.vue"),
        props: { kind: "delivery-note" },
      },
      {
        path: "delivery-notes/new",
        name: "delivery-note-new",
        component: () => import("@/views/trade/DeliveryNoteFormView.vue"),
      },
      {
        path: "delivery-notes/:id",
        name: "delivery-note-detail",
        component: () => import("@/views/trade/FulfilmentView.vue"),
        props: (route) => ({ kind: "delivery-note", id: route.params.id as string }),
      },
      // Module 04 — Buying
      {
        path: "purchase-orders",
        name: "purchase-orders",
        component: () => import("@/views/trade/OrderListView.vue"),
        props: { kind: "purchase-order" },
      },
      {
        path: "purchase-orders/new",
        name: "purchase-order-new",
        component: () => import("@/views/trade/OrderFormView.vue"),
        props: { kind: "purchase-order" },
      },
      {
        path: "purchase-orders/:id",
        name: "purchase-order-detail",
        component: () => import("@/views/trade/OrderFormView.vue"),
        props: (route) => ({ kind: "purchase-order", id: route.params.id as string }),
      },
      { path: "sourcing", name: "sourcing", component: () => import("@/views/buying/RfqView.vue") },
      // Module 05 — Selling
      {
        path: "quotations",
        name: "quotations",
        component: () => import("@/views/trade/OrderListView.vue"),
        props: { kind: "quotation" },
      },
      {
        path: "quotations/new",
        name: "quotation-new",
        component: () => import("@/views/trade/OrderFormView.vue"),
        props: { kind: "quotation" },
      },
      {
        path: "quotations/:id",
        name: "quotation-detail",
        component: () => import("@/views/trade/OrderFormView.vue"),
        props: (route) => ({ kind: "quotation", id: route.params.id as string }),
      },
      {
        path: "cm-plans",
        name: "cm-plans",
        component: () => import("@/views/selling/ContributionMarginPlanListView.vue"),
      },
      {
        path: "cm-plans/:id",
        name: "cm-plan-detail",
        component: () => import("@/views/selling/ContributionMarginPlanView.vue"),
      },
      {
        path: "cm-planning-settings",
        name: "cm-planning-settings",
        component: () => import("@/views/selling/CmPlanningSettingsView.vue"),
      },
      {
        path: "sales-orders",
        name: "sales-orders",
        component: () => import("@/views/trade/OrderListView.vue"),
        props: { kind: "sales-order" },
      },
      {
        path: "sales-orders/new",
        name: "sales-order-new",
        component: () => import("@/views/trade/OrderFormView.vue"),
        props: { kind: "sales-order" },
      },
      {
        path: "sales-orders/:id",
        name: "sales-order-detail",
        component: () => import("@/views/trade/OrderFormView.vue"),
        props: (route) => ({ kind: "sales-order", id: route.params.id as string }),
      },
      // Module — Assets (fixed-asset register + depreciation)
      {
        path: "assets",
        name: "assets",
        component: () => import("@/views/assets/AssetView.vue"),
      },
      {
        path: "asset-reports",
        name: "asset-reports",
        component: () => import("@/views/assets/AssetReportsView.vue"),
      },
      {
        path: "asset-capitalize",
        name: "asset-capitalize",
        component: () => import("@/views/assets/AssetCapitalizeView.vue"),
      },
      {
        path: "assets/:id",
        name: "asset-detail",
        component: () => import("@/views/assets/AssetDetailView.vue"),
        props: true,
      },
      // Metadata engine ("the machine") — generic list/form for any registered DocType.
      // Adding a master needs NO new route here; it is reached via /m/<slug>.
      {
        path: "m/:doctype",
        name: "generic-list",
        component: () => import("@/views/generic/GenericListView.vue"),
        props: true,
      },
      {
        path: "m/:doctype/:id",
        name: "generic-form",
        component: () => import("@/views/generic/GenericFormView.vue"),
        props: true,
      },
      // ERPNext-style module workspaces — rendered INSIDE the shell so the
      // module's context-aware sidebar persists across all its pages.
      {
        path: "selling",
        name: "selling-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "selling" },
      },
      {
        path: "buying",
        name: "buying-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "buying" },
      },
      {
        path: "stock",
        name: "stock-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "stock" },
      },
      {
        path: "accounting",
        name: "accounting-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "accounting" },
      },
      {
        path: "taxation",
        name: "taxation-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "taxation" },
      },
      {
        path: "taxation-settings",
        name: "taxation-settings",
        component: () => import("@/views/compliance/TaxationSettingsView.vue"),
      },
      {
        path: "manufacturing",
        name: "manufacturing-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "manufacturing" },
      },
      // Module — Manufacturing (BOM → Work Order → Manufacture)
      { path: "bom", name: "bom", component: () => import("@/views/manufacturing/BomView.vue") },
      { path: "bom/:id", name: "bom-detail", component: () => import("@/views/manufacturing/BomDetailView.vue"), props: true },
      { path: "work-orders", name: "work-orders", component: () => import("@/views/manufacturing/WorkOrderView.vue") },
      { path: "work-orders/:id", name: "work-order-detail", component: () => import("@/views/manufacturing/WorkOrderDetailView.vue"), props: true },
      { path: "job-cards", name: "job-cards", component: () => import("@/views/manufacturing/JobCardView.vue") },
      { path: "job-cards/:id", name: "job-card-detail", component: () => import("@/views/manufacturing/JobCardDetailView.vue"), props: true },
      { path: "production-plans", name: "production-plans", component: () => import("@/views/manufacturing/ProductionPlanView.vue") },
      { path: "production-plans/:id", name: "production-plan-detail", component: () => import("@/views/manufacturing/ProductionPlanDetailView.vue"), props: true },
      { path: "subcontract-jobs", name: "subcontract-jobs", component: () => import("@/views/manufacturing/SubcontractJobView.vue") },
      { path: "subcontract-jobs/:id", name: "subcontract-job-detail", component: () => import("@/views/manufacturing/SubcontractJobDetailView.vue"), props: true },
      { path: "quality-inspections", name: "quality-inspections", component: () => import("@/views/manufacturing/QualityInspectionView.vue") },
      { path: "quality-inspections/:id", name: "quality-inspection-detail", component: () => import("@/views/manufacturing/QualityInspectionDetailView.vue"), props: true },
      { path: "manufacturing-reports", name: "manufacturing-reports", component: () => import("@/views/manufacturing/ManufacturingReportsView.vue") },
      { path: "manufacturing-planning", name: "manufacturing-planning", component: () => import("@/views/manufacturing/ManufacturingPlanningView.vue") },
      { path: "manufacturing-settings", name: "manufacturing-settings", component: () => import("@/views/manufacturing/ManufacturingSettingsView.vue") },
      // Module 12 — Data Migration (Tally XML/CSV, or a spreadsheet from any app)
      {
        path: "data-migration",
        name: "data-migration-workspace",
        component: () => import("@/views/ModuleWorkspace.vue"),
        props: { moduleKey: "migration" },
      },
      { path: "data-migration/imports", name: "migration-imports", component: () => import("@/views/migration/MigrationImportView.vue") },
      { path: "data-migration/coverage", name: "migration-coverage", component: () => import("@/views/migration/MigrationCoverageView.vue") },
      { path: "data-migration/sources", name: "migration-sources", component: () => import("@/views/migration/MigrationSourcesView.vue") },
      { path: "data-migration/guides/tally", name: "migration-guide-tally", component: () => import("@/views/migration/TallyExportGuideView.vue") },
      { path: "data-migration/imports/:id", name: "migration-import-detail", component: () => import("@/views/migration/MigrationImportDetailView.vue"), props: true },
      // The old /tally/* paths are what testers have bookmarked and what every
      // screenshot in the docs points at. Redirect rather than 404 them.
      { path: "tally", redirect: "/data-migration/imports" },
      { path: "tally/coverage", redirect: "/data-migration/coverage" },
      { path: "tally/guide", redirect: "/data-migration/guides/tally" },
      { path: "tally/imports/:id", redirect: (to) => `/data-migration/imports/${to.params.id}` },
      // Module 13 — Company Secretarial & Governance. One module, two shells: the
      // home view renders a client roster for a practice and a single-entity
      // overview for a business (see docs/SECRETARIAL_GAP_AND_PLAN.md §2.3).
      { path: "secretarial", name: "secretarial-home", component: () => import("@/views/secretarial/SecretarialHomeView.vue") },
      { path: "secretarial/company", name: "secretarial-company", component: () => import("@/views/secretarial/CompanyProfileView.vue") },
      { path: "secretarial/clients", name: "secretarial-clients", component: () => import("@/views/secretarial/PracticeClientsView.vue") },
      { path: "secretarial/directors", name: "secretarial-directors", component: () => import("@/views/secretarial/DirectorsView.vue") },
      { path: "secretarial/registers/:slug", name: "secretarial-register", component: () => import("@/views/secretarial/RegisterView.vue"), props: true },
      { path: "secretarial/compliance", name: "secretarial-compliance", component: () => import("@/views/secretarial/ComplianceCalendarView.vue") },
      { path: "secretarial/rules", name: "secretarial-rules", component: () => import("@/views/secretarial/RulesReviewView.vue") },
      { path: "secretarial/access", name: "secretarial-access", component: () => import("@/views/secretarial/EngagementsView.vue") },
      // Phases 2-3 — the document engine and the governance thread.
      { path: "secretarial/meetings", name: "secretarial-meetings", component: () => import("@/views/secretarial/MeetingsView.vue") },
      { path: "secretarial/meetings/:id", name: "secretarial-meeting", component: () => import("@/views/secretarial/MeetingDetailView.vue"), props: true },
      { path: "secretarial/circulars", name: "secretarial-circulars", component: () => import("@/views/secretarial/CircularsView.vue") },
      { path: "secretarial/circulars/:id", name: "secretarial-circular", component: () => import("@/views/secretarial/CircularDetailView.vue"), props: true },
      { path: "secretarial/documents", name: "secretarial-documents", component: () => import("@/views/secretarial/DocumentsView.vue") },
      { path: "secretarial/ctcs", name: "secretarial-ctcs", component: () => import("@/views/secretarial/CtcsView.vue") },
      { path: "secretarial/filings", name: "secretarial-filings", component: () => import("@/views/secretarial/FilingsView.vue") },
      { path: "secretarial/facts", name: "secretarial-facts", component: () => import("@/views/secretarial/FactsView.vue") },
      { path: "secretarial/capital", name: "secretarial-capital", component: () => import("@/views/secretarial/CapitalView.vue") },
      { path: "secretarial/s186", name: "secretarial-s186", component: () => import("@/views/secretarial/S186View.vue") },
      // Module 06+ routes register here per module
    ],
  },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.initialized) {
    await auth.restoreSession();
  }
  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (to.name === "login" && auth.isAuthenticated) {
    return { name: "dashboard" };
  }
});
