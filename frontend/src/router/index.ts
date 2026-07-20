// Lazy-loaded routes per module with an auth guard.

import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/auth/LoginView.vue"),
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
        path: "income-tax",
        name: "income-tax",
        component: () => import("@/views/compliance/IncomeTaxView.vue"),
      },
      {
        path: "income-tax/new",
        name: "income-tax-new",
        component: () => import("@/views/compliance/IncomeTaxView.vue"),
      },
      {
        path: "income-tax/:id",
        name: "income-tax-detail",
        component: () => import("@/views/compliance/IncomeTaxView.vue"),
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
      { path: "manufacturing-reports", name: "manufacturing-reports", component: () => import("@/views/manufacturing/ManufacturingReportsView.vue") },
      { path: "manufacturing-settings", name: "manufacturing-settings", component: () => import("@/views/manufacturing/ManufacturingSettingsView.vue") },
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
