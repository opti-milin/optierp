// Config for module workspace pages (ERPNext-style), shared by ModuleWorkspace.vue.
// Data, not code: each module defines its sidebar + "Reports & Masters" grid here.
// `to` is a router path; links without `to` (planned:true) render greyed.

export interface WsLink {
  label: string;
  to?: string;
  planned?: boolean;
}

export interface WsGroup {
  title: string;
  links: WsLink[];
}

export interface WsNavItem {
  label: string;
  to: string;
  icon?: string;
}

export interface WsNavGroup {
  title?: string;
  items: WsNavItem[];
}

export interface WorkspaceConfig {
  key: string;
  title: string;
  statsEndpoint: string;
  sidebar: WsNavGroup[];
  cards: WsGroup[];
}

const SELLING: WorkspaceConfig = {
  key: "selling",
  title: "Sales",
  statsEndpoint: "/selling/workspace",
  sidebar: [
    {
      items: [
        { label: "Home", to: "/", icon: "⌂" },
        { label: "Dashboard", to: "/selling", icon: "▦" },
        { label: "Quotation", to: "/quotations", icon: "📝" },
        { label: "Sales Order", to: "/sales-orders", icon: "🛒" },
        { label: "Sales Invoice", to: "/sales-invoices", icon: "🧾" },
        { label: "Delivery Note", to: "/delivery-notes", icon: "🚚" },
      ],
    },
    {
      title: "Setup",
      items: [
        { label: "Customer", to: "/m/customer" },
        { label: "Address", to: "/m/address" },
        { label: "Campaign", to: "/m/campaign" },
        { label: "Contact", to: "/m/contact" },
        { label: "Customer Group", to: "/m/customer-group" },
        { label: "Monthly Distribution", to: "/m/monthly-distribution" },
        { label: "Sales Partner", to: "/m/sales-partner" },
        { label: "Sales Person", to: "/m/sales-person" },
        { label: "Territory", to: "/m/territory" },
        { label: "Payment Terms Template", to: "/m/payment-terms-template" },
        { label: "Tax Category", to: "/m/tax-category" },
        { label: "Terms Template", to: "/m/terms-template" },
        { label: "UTM Source", to: "/m/utm-source" },
      ],
    },
    {
      title: "Items & Pricing",
      items: [
        { label: "Products & Services", to: "/items" },
        { label: "Pricing Rule", to: "/m/pricing-rule" },
      ],
    },
    { title: "Reports", items: [{ label: "Reports", to: "/reports" }] },
  ],
  cards: [
    {
      title: "Sales",
      links: [
        { label: "Customer", to: "/m/customer" },
        { label: "Quotation", to: "/quotations" },
        { label: "Sales Order", to: "/sales-orders" },
        { label: "Sales Invoice", to: "/sales-invoices" },
        { label: "Delivery Note", to: "/delivery-notes" },
        { label: "Blanket Order", to: "/m/blanket-order" },
        { label: "Sales Partner", to: "/m/sales-partner" },
        { label: "Sales Person", to: "/m/sales-person" },
      ],
    },
    {
      title: "Setup",
      links: [
        { label: "Address", to: "/m/address" },
        { label: "Campaign", to: "/m/campaign" },
        { label: "Contact", to: "/m/contact" },
        { label: "Customer Group", to: "/m/customer-group" },
        { label: "Monthly Distribution", to: "/m/monthly-distribution" },
        { label: "Territory", to: "/m/territory" },
        { label: "Terms Template", to: "/m/terms-template" },
        { label: "UTM Source", to: "/m/utm-source" },
      ],
    },
    {
      title: "Items & Pricing",
      links: [
        { label: "Products & Services", to: "/items" },
        { label: "Pricing Rule", to: "/m/pricing-rule" },
        { label: "Promotional Scheme", to: "/m/promotional-scheme" },
        { label: "Coupon Code", to: "/m/coupon-code" },
        { label: "Item Group", planned: true },
        { label: "Price List", planned: true },
        { label: "Item Price", planned: true },
        { label: "Shipping Rule", to: "/m/shipping-rule" },
        { label: "Product Bundle", to: "/m/product-bundle" },
      ],
    },
    {
      title: "Settings",
      links: [
        { label: "Tax Template", planned: true },
        { label: "Terms Template", to: "/m/terms-template" },
        { label: "Sales Settings", to: "/settings" },
      ],
    },
    {
      title: "Key Reports",
      links: [
        { label: "Financial Reports", to: "/reports" },
        { label: "Stock Balance", to: "/stock-balance" },
      ],
    },
    {
      title: "Point of Sale",
      links: [
        { label: "POS Profile", planned: true },
        { label: "POS Invoice", planned: true },
        { label: "Loyalty Program", planned: true },
      ],
    },
  ],
};

const BUYING: WorkspaceConfig = {
  key: "buying",
  title: "Purchases",
  statsEndpoint: "/buying/workspace",
  sidebar: [
    {
      items: [
        { label: "Home", to: "/", icon: "⌂" },
        { label: "Dashboard", to: "/buying", icon: "▦" },
        { label: "Material Request", to: "/material-requests", icon: "📋" },
        { label: "Request for Quotation", to: "/sourcing", icon: "📨" },
        { label: "Purchase Order", to: "/purchase-orders", icon: "🛍" },
        { label: "Purchase Receipt", to: "/purchase-receipts", icon: "📦" },
        { label: "Purchase Invoice", to: "/purchase-invoices", icon: "📥" },
      ],
    },
    {
      title: "Setup",
      items: [
        { label: "Supplier", to: "/m/supplier" },
        { label: "Supplier Group", to: "/m/supplier-group" },
        { label: "Address", to: "/m/address" },
        { label: "Contact", to: "/m/contact" },
        { label: "Payment Terms Template", to: "/m/payment-terms-template" },
        { label: "Tax Category", to: "/m/tax-category" },
        { label: "Terms Template", to: "/m/terms-template" },
        { label: "Products & Services", to: "/items" },
      ],
    },
    { title: "Reports", items: [{ label: "Reports", to: "/reports" }] },
  ],
  cards: [
    {
      title: "Purchases",
      links: [
        { label: "Supplier", to: "/m/supplier" },
        { label: "Material Request", to: "/material-requests" },
        { label: "Request for Quotation", to: "/sourcing" },
        { label: "Supplier Quotation", to: "/sourcing" },
        { label: "Purchase Order", to: "/purchase-orders" },
        { label: "Purchase Receipt", to: "/purchase-receipts" },
        { label: "Purchase Invoice", to: "/purchase-invoices" },
      ],
    },
    {
      title: "Setup",
      links: [
        { label: "Supplier", to: "/m/supplier" },
        { label: "Supplier Group", to: "/m/supplier-group" },
        { label: "Address", to: "/m/address" },
        { label: "Contact", to: "/m/contact" },
        { label: "Terms Template", to: "/m/terms-template" },
      ],
    },
    {
      title: "Items & Pricing",
      links: [
        { label: "Products & Services", to: "/items" },
        { label: "Item Group", planned: true },
        { label: "Price List", planned: true },
        { label: "Item Price", planned: true },
        { label: "Pricing Rule", planned: true },
        { label: "Product Bundle", planned: true },
      ],
    },
    {
      title: "Settings",
      links: [
        { label: "Tax Template", planned: true },
        { label: "Terms Template", to: "/m/terms-template" },
        { label: "Purchases Settings", to: "/settings" },
      ],
    },
    {
      title: "Key Reports",
      links: [
        { label: "Financial Reports", to: "/reports" },
        { label: "Stock Balance", to: "/stock-balance" },
      ],
    },
  ],
};

const STOCK: WorkspaceConfig = {
  key: "stock",
  title: "Inventory",
  statsEndpoint: "/stock/workspace",
  sidebar: [
    {
      items: [
        { label: "Home", to: "/", icon: "⌂" },
        { label: "Dashboard", to: "/stock", icon: "▦" },
        { label: "Products & Services", to: "/items", icon: "🏷" },
        { label: "Warehouse", to: "/warehouses", icon: "🏬" },
        { label: "Stock Entry", to: "/stock-entries", icon: "↔" },
        { label: "Stock Reconciliation", to: "/stock-reconciliations", icon: "🧮" },
        { label: "Material Request", to: "/material-requests", icon: "📋" },
        { label: "Reorder", to: "/reorder", icon: "🔁" },
        { label: "Service Credits", to: "/service-credits", icon: "⏱" },
        { label: "Stock Balance", to: "/stock-balance", icon: "📈" },
        { label: "Stock Ledger", to: "/stock-balance?tab=ledger", icon: "📜" },
        { label: "Stock Ageing", to: "/stock-balance?tab=ageing", icon: "⏳" },
        { label: "Serial Numbers", to: "/serial-nos", icon: "🔢" },
        { label: "Batches", to: "/m/batch", icon: "🏷️" },
        { label: "Purchase Receipt", to: "/purchase-receipts", icon: "📦" },
        { label: "Delivery Note", to: "/delivery-notes", icon: "🚚" },
      ],
    },
    {
      title: "Reports",
      items: [
        { label: "Stock Balance", to: "/stock-balance" },
        { label: "Stock Ledger", to: "/stock-balance?tab=ledger" },
        { label: "Stock Ageing", to: "/stock-balance?tab=ageing" },
        { label: "Reorder", to: "/reorder" },
        { label: "General Ledger", to: "/reports?tab=general-ledger" },
        { label: "Financial Reports", to: "/reports" },
      ],
    },
  ],
  cards: [
    {
      title: "Stock Transactions",
      links: [
        { label: "Stock Entry", to: "/stock-entries" },
        { label: "Stock Reconciliation", to: "/stock-reconciliations" },
        { label: "Material Request", to: "/material-requests" },
        { label: "Reorder", to: "/reorder" },
        { label: "Service Credits", to: "/service-credits" },
        { label: "Purchase Receipt", to: "/purchase-receipts" },
        { label: "Delivery Note", to: "/delivery-notes" },
        { label: "Stock Balance", to: "/stock-balance" },
      ],
    },
    {
      title: "Items & Pricing",
      links: [
        { label: "Products & Services", to: "/items" },
        { label: "Item Group", planned: true },
        { label: "Price List", planned: true },
        { label: "Item Price", planned: true },
        { label: "Product Bundle", planned: true },
      ],
    },
    {
      title: "Setup",
      links: [
        { label: "Warehouse", to: "/warehouses" },
        { label: "Item Group", planned: true },
        { label: "UOM", planned: true },
        { label: "Brand", planned: true },
      ],
    },
    {
      title: "Key Reports",
      links: [
        { label: "Stock Balance", to: "/stock-balance" },
        { label: "Stock Ledger", to: "/stock-balance?tab=ledger" },
        { label: "Stock Ageing", to: "/stock-balance?tab=ageing" },
        { label: "Reorder", to: "/reorder" },
        { label: "General Ledger", to: "/reports?tab=general-ledger" },
        { label: "Financial Reports", to: "/reports" },
      ],
    },
  ],
};

// Accounting is organised into ERPNext-style sub-modules: each card below is one
// sub-module (Invoicing, Payments, General Ledger, Taxes, Banking, Budget,
// Accounts Setup, Financial Reports) grouping its screens, and the sidebar
// mirrors the same grouping. Share Management + Subscription are flagged
// `planned` (not built yet) to show the full sub-module set.
const ACCOUNTING: WorkspaceConfig = {
  key: "accounting",
  title: "Accounting",
  statsEndpoint: "/accounting/workspace",
  sidebar: [
    {
      items: [
        { label: "Home", to: "/", icon: "⌂" },
        { label: "Dashboard", to: "/accounting", icon: "▦" },
      ],
    },
    {
      title: "Invoicing",
      items: [
        { label: "Sales Invoice", to: "/sales-invoices", icon: "🧾" },
        { label: "Purchase Invoice", to: "/purchase-invoices", icon: "📥" },
        { label: "Opening Invoices", to: "/opening-invoices" },
      ],
    },
    {
      title: "Payments",
      items: [
        { label: "Payment Entry", to: "/payment-entries", icon: "💸" },
        { label: "Payment Reconciliation", to: "/payment-reconciliation" },
        { label: "Statement of Accounts", to: "/statements", icon: "🧾" },
        { label: "Payment Requests", to: "/payment-requests", icon: "💳" },
        { label: "Subscriptions", to: "/subscriptions", icon: "🔁" },
        { label: "Subscription Plan", to: "/m/subscription-plan" },
        { label: "Dunning (Reminders)", to: "/dunning", icon: "⏰" },
        { label: "Dunning Type", to: "/m/dunning-type" },
      ],
    },
    {
      title: "General Ledger",
      items: [
        { label: "Journal Entry", to: "/journal-entries", icon: "📒" },
        { label: "Chart of Accounts", to: "/chart-of-accounts" },
        { label: "General Ledger", to: "/reports?tab=general-ledger" },
      ],
    },
    {
      title: "Taxes",
      items: [
        { label: "Tax Template", to: "/tax-templates" },
        { label: "Item Tax Template", to: "/m/item-tax-template" },
        { label: "Tax Category", to: "/m/tax-category" },
        { label: "TDS / TCS", to: "/m/tax-withholding-category" },
        { label: "GST Settings", to: "/gst-settings" },
        { label: "GST Returns (GSTR-1/3B)", to: "/gst-returns" },
        { label: "TDS Returns (26Q / 16A)", to: "/tds-returns" },
      ],
    },
    {
      title: "Banking",
      items: [
        { label: "Bank", to: "/m/bank" },
        { label: "Bank Account", to: "/m/bank-account" },
        { label: "Bank Reconciliation", to: "/bank-reconciliation" },
        { label: "Uncleared Items", to: "/reports?tab=bank-recon" },
      ],
    },
    {
      title: "Budget",
      items: [
        { label: "Budget", to: "/budgets" },
        { label: "Budget Variance", to: "/reports?tab=budget-variance" },
      ],
    },
    {
      title: "Share Management",
      items: [
        { label: "Share Transfer", to: "/share-transfers", icon: "📜" },
        { label: "Cap Table", to: "/reports?tab=share-balance" },
        { label: "Share Type", to: "/m/share-type" },
        { label: "Shareholder", to: "/m/shareholder" },
      ],
    },
    {
      title: "Accounts Setup",
      items: [
        { label: "Fiscal Year", to: "/m/fiscal-year" },
        { label: "Cost Center", to: "/m/cost-center" },
        { label: "Mode of Payment", to: "/m/mode-of-payment" },
        { label: "Payment Terms Template", to: "/m/payment-terms-template" },
        { label: "Terms Template", to: "/m/terms-template" },
      ],
    },
    {
      title: "Reports",
      items: [
        { label: "Financial Reports", to: "/reports" },
        { label: "Gross Profit", to: "/reports?tab=gross-profit" },
      ],
    },
  ],
  cards: [
    {
      title: "Invoicing",
      links: [
        { label: "Sales Invoice", to: "/sales-invoices" },
        { label: "Purchase Invoice", to: "/purchase-invoices" },
        { label: "Opening Invoices", to: "/opening-invoices" },
      ],
    },
    {
      title: "Payments",
      links: [
        { label: "Payment Entry", to: "/payment-entries" },
        { label: "Payment Reconciliation", to: "/payment-reconciliation" },
      ],
    },
    {
      title: "General Ledger",
      links: [
        { label: "Journal Entry", to: "/journal-entries" },
        { label: "Chart of Accounts", to: "/chart-of-accounts" },
        { label: "General Ledger", to: "/reports?tab=general-ledger" },
      ],
    },
    {
      title: "Taxes",
      links: [
        { label: "Tax Template", to: "/tax-templates" },
        { label: "Item Tax Template", to: "/m/item-tax-template" },
        { label: "Tax Category", to: "/m/tax-category" },
        { label: "TDS / TCS Category", to: "/m/tax-withholding-category" },
        { label: "GST Settings", to: "/gst-settings" },
        { label: "GST Returns (GSTR-1/3B)", to: "/gst-returns" },
        { label: "TDS Returns (26Q / 16A)", to: "/tds-returns" },
      ],
    },
    {
      title: "Banking",
      links: [
        { label: "Bank", to: "/m/bank" },
        { label: "Bank Account", to: "/m/bank-account" },
        { label: "Bank Reconciliation", to: "/bank-reconciliation" },
        { label: "Uncleared Items", to: "/reports?tab=bank-recon" },
      ],
    },
    {
      title: "Budget",
      links: [
        { label: "Budget", to: "/budgets" },
        { label: "Budget Variance", to: "/reports?tab=budget-variance" },
      ],
    },
    {
      title: "Accounts Setup",
      links: [
        { label: "Fiscal Year", to: "/m/fiscal-year" },
        { label: "Cost Center", to: "/m/cost-center" },
        { label: "Mode of Payment", to: "/m/mode-of-payment" },
        { label: "Payment Terms Template", to: "/m/payment-terms-template" },
        { label: "Terms Template", to: "/m/terms-template" },
      ],
    },
    {
      title: "Financial Reports",
      links: [
        { label: "Trial Balance", to: "/reports?tab=trial-balance" },
        { label: "Profit & Loss", to: "/reports?tab=profit-loss" },
        { label: "Balance Sheet", to: "/reports?tab=balance-sheet" },
        { label: "Gross Profit", to: "/reports?tab=gross-profit" },
        { label: "Receivable / Payable", to: "/reports?tab=receivable" },
      ],
    },
    {
      title: "Share Management",
      links: [
        { label: "Share Transfer", to: "/share-transfers" },
        { label: "Cap Table", to: "/reports?tab=share-balance" },
        { label: "Share Type", to: "/m/share-type" },
        { label: "Shareholder", to: "/m/shareholder" },
      ],
    },
    {
      title: "Subscription",
      links: [
        { label: "Subscriptions", to: "/subscriptions" },
        { label: "Subscription Plan", to: "/m/subscription-plan" },
      ],
    },
  ],
};

// Assets module (Phase 1): the fixed-asset register + its two engine masters.
// `/assets` is the register list itself (not a separate dashboard), so statsEndpoint
// is unused until a workspace dashboard lands in a later phase.
const ASSETS: WorkspaceConfig = {
  key: "assets",
  title: "Assets",
  statsEndpoint: "/assets/workspace",
  sidebar: [
    {
      items: [
        { label: "Home", to: "/", icon: "⌂" },
        { label: "Assets", to: "/assets", icon: "🏗" },
        { label: "Capitalize Asset", to: "/asset-capitalize", icon: "🧱" },
      ],
    },
    {
      title: "Maintenance",
      items: [
        { label: "Maintenance & Repair", to: "/m/asset-maintenance" },
        { label: "Maintenance Due", to: "/asset-reports?tab=maintenance" },
      ],
    },
    {
      title: "Setup",
      items: [
        { label: "Asset Category", to: "/m/asset-category" },
        { label: "Location", to: "/m/location" },
      ],
    },
    {
      title: "Reports",
      items: [
        { label: "Fixed Asset Register", to: "/asset-reports" },
        { label: "Depreciation Ledger", to: "/asset-reports?tab=ledger" },
        { label: "General Ledger", to: "/reports?tab=general-ledger" },
      ],
    },
  ],
  cards: [
    {
      title: "Assets",
      links: [
        { label: "Asset Register", to: "/assets" },
        { label: "Asset Reports", to: "/asset-reports" },
        { label: "Asset Category", to: "/m/asset-category" },
        { label: "Location", to: "/m/location" },
      ],
    },
    {
      title: "Maintenance",
      links: [
        { label: "Maintenance & Repair", to: "/m/asset-maintenance" },
      ],
    },
  ],
};

export const WORKSPACES: Record<string, WorkspaceConfig> = {
  selling: SELLING,
  buying: BUYING,
  stock: STOCK,
  accounting: ACCOUNTING,
  assets: ASSETS,
};
