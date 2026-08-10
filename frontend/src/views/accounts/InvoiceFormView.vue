<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import PrintButton from "@/components/shared/PrintButton.vue";
import SendEmailButton from "@/components/shared/SendEmailButton.vue";
import ItemsGrid, { type GridColumn } from "@/components/shared/ItemsGrid.vue";
import GetItemsFrom, { type ItemSource } from "@/components/shared/GetItemsFrom.vue";
import DateField from "@/components/shared/DateField.vue";
import TaxesCharges from "@/components/shared/TaxesCharges.vue";
import AdditionalDiscount, { type DiscountModel } from "@/components/shared/AdditionalDiscount.vue";
import CurrencySection, { type CurrencyModel } from "@/components/shared/CurrencySection.vue";
import DocumentTotals from "@/components/shared/DocumentTotals.vue";
import PaymentSchedulePreview from "@/components/shared/PaymentSchedulePreview.vue";
import { computeTotals } from "@/utils/totals";
import DataEntry, { type ImportedRow } from "@/components/shared/DataEntry.vue";
import AddressContactTab, { type AddressContactModel } from "@/components/shared/AddressContactTab.vue";
import AddressContactSummary from "@/components/shared/AddressContactSummary.vue";
import { rowKey } from "@/utils/rowKey";
import { useAccountsStore } from "@/stores/accounts";
import { useStockStore } from "@/stores/stock";
import { useCoreStore } from "@/stores/core";
import { useAuthStore } from "@/stores/auth";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { api } from "@/api/client";
import { formatCurrency, formatDate, formatQty } from "@/utils/format";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  InvoiceDetail,
  InvoiceItemIn,
  InvoiceListItem,
  PurchaseInvoiceDetail,
  SalesInvoiceDetail,
  TaxRowIn,
  UnreconciledPaymentRow,
  UnreconciledResponse,
} from "@/types/accounts";

const props = defineProps<{ kind: "sales" | "purchase"; id?: string }>();
const router = useRouter();
const route = useRoute();
const store = useAccountsStore();
const stock = useStockStore();
const core = useCoreStore();
const auth = useAuthStore();
const companyCurrency = useCompanyCurrency();

const endpoint = computed(() => (props.kind === "sales" ? "/sales-invoices" : "/purchase-invoices"));
const partyLabel = computed(() => (props.kind === "sales" ? "Customer" : "Supplier"));
const parties = computed(() => (props.kind === "sales" ? store.customers : store.suppliers));

const docPartyName = computed(() => {
  if (!doc.value) return "";
  return props.kind === "sales"
    ? ((doc.value as SalesInvoiceDetail).customer_name ?? "")
    : ((doc.value as PurchaseInvoiceDetail).supplier_name ?? "");
});
const docBillNo = computed(() =>
  props.kind === "purchase" ? (doc.value as PurchaseInvoiceDetail | null)?.bill_no : null,
);
const money = (value: string | number | null | undefined): string =>
  formatCurrency(value, doc.value?.currency ?? "INR");

// A tax row stores the *template's* nominal rate (e.g. 9%) and a description
// like "CGST @ 9%". When an Item Tax Template overrides a line (e.g. a 5%-GST
// item under an 18% invoice template), the charged amount is lower than the
// nominal rate implies — so showing "@ 9%" is misleading. These helpers show
// the EFFECTIVE rate actually charged (tax_amount ÷ net_total) and strip the
// baked-in "@ N%" from the label so the two never contradict each other.
const taxEffectiveRate = (
  tax: { rate: string | number; tax_amount: string | number | null },
): number | null => {
  const net = Number(doc.value?.net_total ?? 0);
  if (!net || !Number(tax.rate)) return null; // rate 0 ⇒ a fixed "Actual" charge
  return Math.round((Number(tax.tax_amount) / net) * 10000) / 100;
};
const taxLabel = (tax: { description?: string | null; charge_type?: string }): string =>
  (tax.description || tax.charge_type || "").replace(/\s*@\s*[\d.]+\s*%/, "").trim();

const doc = ref<InvoiceDetail | null>(null);
const error = ref<ErrorEnvelope | null>(null);
const saving = ref(false);

const partyId = ref("");
const newPartyName = ref("");
// Debit-To (receivable) for sales / Credit-To (payable) for purchase. The
// backend defaults these from customer/company when left blank.
const controlAccountId = ref("");
const controlAccountLabel = computed(() =>
  props.kind === "sales" ? "Debit To (Receivable)" : "Credit To (Payable)",
);
const controlAccountOptions = computed(() =>
  props.kind === "sales" ? store.receivableAccountOptions : store.payableAccountOptions,
);
const postingDate = ref(new Date().toISOString().slice(0, 10));
const dueDate = ref("");
// India GST: place of supply ("NN-State") defaults on the server from the party/company
// GSTIN when left blank; reverse charge = recipient pays the GST.
const placeOfSupply = ref("");
const isReverseCharge = ref(false);
const items = ref<InvoiceItemIn[]>([{ item_name: "", qty: 1, rate: 0 }]);
const taxes = ref<TaxRowIn[]>([]);
const taxTemplateId = ref("");
// India withholding: TDS on purchase (withheld from supplier) / TCS on sales
// (collected from customer), for the chosen category. Same field, opposite sign.
const tdsCategoryId = ref("");
const tdsCategories = ref<Array<{ id: string; category_name: string; rate: string }>>([]);
// live preview of the chosen TDS/TCS in the totals block (backend computes the real amount on save)
const withholdingPreview = computed(() => {
  const cat = tdsCategories.value.find((c) => c.id === tdsCategoryId.value);
  if (!cat) return undefined;
  return props.kind === "sales"
    ? { label: "TCS collected", rate: Number(cat.rate), sign: 1, netLabel: "Customer pays" }
    : { label: "TDS withheld", rate: Number(cat.rate), sign: -1, netLabel: "Net payable to supplier" };
});

// Picking a Taxes & Charges template fills the editable rows (ERPNext model:
// the rows stay authoritative and editable; we don't send the template id).
function applyTaxTemplate(): void {
  const tpl = store.taxTemplates.find((t) => t.id === taxTemplateId.value);
  if (!tpl) return;
  taxes.value = tpl.details.map((d) => ({
    charge_type: d.charge_type,
    rate: Number(d.rate),
    tax_amount: Number(d.tax_amount),
    row_id: d.row_id,
    account_head_id: d.account_head_id,
    description: d.description,
    add_deduct_tax: d.add_deduct_tax,
    category: d.category,
    included_in_print_rate: d.included_in_print_rate,
  }));
}
const discount = ref<DiscountModel>({
  apply_discount_on: "Grand Total",
  additional_discount_percentage: 0,
  discount_amount: 0,
});

// Live GST preview (sales): the server computes the taxes create() will apply —
// from each line's Item Tax Template / HSN — so the totals show CGST/SGST/IGST
// while drafting, before anything is saved. Display-only; NOT sent on save (the
// backend recomputes). Skipped once the user manually fills the tax rows.
const previewTaxes = ref<TaxRowIn[]>([]);
let previewTimer: ReturnType<typeof setTimeout> | undefined;

async function refreshTaxPreview(): Promise<void> {
  const lines = items.value.filter((i) => i.item_name);
  if (!partyId.value || !lines.length || taxes.value.length) {
    previewTaxes.value = [];
    return;
  }
  const partyKey = props.kind === "sales" ? "customer_id" : "supplier_id";
  try {
    const { data } = await api.post<{
      taxes: Array<{ description: string; rate: string; tax_amount: string }>;
    }>(`${endpoint.value}/preview`, {
      [partyKey]: partyId.value,
      posting_date: postingDate.value,
      place_of_supply: placeOfSupply.value || null,
      // reverse charge (purchase): self-assessed GST nets the payable to the base
      is_reverse_charge: isReverseCharge.value,
      apply_discount_on: discount.value.apply_discount_on,
      additional_discount_percentage: discount.value.additional_discount_percentage || 0,
      discount_amount: discount.value.discount_amount || 0,
      items: lines,
    });
    previewTaxes.value = data.taxes.map((t) => ({
      charge_type: "Actual",
      account_head_id: "",
      description: t.description,
      rate: Number(t.rate),
      tax_amount: Number(t.tax_amount),
    })) as unknown as TaxRowIn[];
  } catch {
    previewTaxes.value = [];
  }
}

watch(
  [items, partyId, placeOfSupply, isReverseCharge, discount, () => taxes.value.length],
  () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => void refreshTaxPreview(), 400);
  },
  { deep: true },
);

// The manually-entered tax rows win; otherwise show the server GST preview so
// the totals block reflects the GST that will be charged.
const effectiveTaxes = computed(() =>
  taxes.value.length ? taxes.value : previewTaxes.value,
);
const currencyModel = ref<CurrencyModel>({ currency: "", conversion_rate: 1 });
const isReturn = ref(false);
const returnAgainstId = ref("");
const returnables = ref<InvoiceListItem[]>([]);
const terms = ref("");
const termsTemplates = ref<Array<{ id: string; template_name: string; terms: string | null }>>([]);
const termsTemplateId = ref("");

// Payment Terms (the payment split). Store only the template id; the due-date
// breakdown is derived live from the grand total + posting date.
const paymentTermsTemplateId = ref("");
const paymentTermsTemplates = ref<Array<{ id: string; template_name: string }>>([]);
async function fetchPaymentTermsTemplates(): Promise<void> {
  try {
    const resp = await api.get<{ items: Array<{ id: string; template_name: string }> }>(
      "/registry/payment-terms-template",
      { params: { page_size: 200 } },
    );
    paymentTermsTemplates.value = resp.data.items ?? [];
  } catch {
    paymentTermsTemplates.value = [];
  }
}
const liveGrandTotal = computed(() => {
  const t = computeTotals(
    items.value as unknown as Parameters<typeof computeTotals>[0],
    effectiveTaxes.value as unknown as Parameters<typeof computeTotals>[1],
    discount.value as unknown as Parameters<typeof computeTotals>[2],
  );
  return t.roundedTotal || t.grandTotal;
});

// Picking a Terms Template fills the editable Terms text (stays editable).
async function fetchTermsTemplates(): Promise<void> {
  try {
    const resp = await api.get<{ items: Array<{ id: string; template_name: string; terms: string | null }> }>(
      "/registry/terms-template",
      { params: { page_size: 200 } },
    );
    termsTemplates.value = resp.data.items ?? [];
  } catch {
    termsTemplates.value = [];
  }
}
function applyTermsTemplate(): void {
  const tpl = termsTemplates.value.find((t) => t.id === termsTemplateId.value);
  if (tpl) terms.value = tpl.terms ?? "";
}

const poNo = ref("");
const poDate = ref("");
const billNo = ref(""); // supplier's invoice number (purchase)
const billDate = ref("");
const addressContact = ref<AddressContactModel>({
  billing_address_id: null,
  shipping_address_id: null,
  contact_person_id: null,
});

// Map the generic A&C picks to the party-specific billing-address column.
function acPayload(party: "customer" | "supplier"): Record<string, unknown> {
  return {
    [`${party}_address_id`]: addressContact.value.billing_address_id,
    shipping_address_id: addressContact.value.shipping_address_id,
    contact_person_id: addressContact.value.contact_person_id,
  };
}

const companyName = computed(
  () => core.companies.find((c) => c.id === auth.companyId)?.company_name ?? "",
);

const activeTab = ref("Details");
const tabs = ["Details", "Payments", "Address & Contact", "Terms", "More Info"];

// Show the Disc % column on the detail table only when a line carries one.
const hasLineDiscount = computed(() =>
  (doc.value?.items ?? []).some((it) => Number(it.discount_percentage) > 0),
);

// Resolve the chosen payment-terms template name for the summary header.
const paymentTermsName = computed(
  () =>
    paymentTermsTemplates.value.find((t) => t.id === doc.value?.payment_terms_template_id)
      ?.template_name ?? null,
);

const META = {
  sales: { module: "Sales", title: "Sales Invoice", series: "SINV-.YY.-", newRoute: "sales-invoice-new" },
  purchase: { module: "Purchases", title: "Purchase Invoice", series: "PINV-.YY.-", newRoute: "purchase-invoice-new" },
} as const;
const meta = computed(() => META[props.kind]);

// Stock-qty display: blank unless the (item-linked) line uses a non-stock UOM
function stockQtyLabel(row: Record<string, unknown>): string {
  const id = row.item_id as string | null;
  if (!id) return "";
  const f = stock.uomFactor(id, row.uom as string | undefined);
  if (f === 1) return "";
  return String(Math.round((Number(row.qty) || 0) * f * 1000) / 1000);
}

const gridColumns = computed<GridColumn[]>(() => [
  { key: "item_id", label: "Item / Service", type: "item", required: true, freeText: true, nameKey: "item_name" },
  { key: "qty", label: "Quantity", type: "number", align: "right", required: true },
  { key: "uom", label: "UOM", type: "text" },
  { key: "stock_qty", label: "Stock Qty", type: "computed", align: "right", compute: stockQtyLabel },
  { key: "rate", label: "Rate", type: "number", align: "right", required: true },
  { key: "discount_percentage", label: "Discount %", type: "number", align: "right" },
  { key: "hsn_sac_code", label: "HSN/SAC", type: "hsn" },
  {
    key: "account_id",
    label: props.kind === "sales" ? "Income Account" : "Expense Account",
    type: "select",
    options: store.accountOptions,
  },
  { key: "cost_center_id", label: "Cost Center", type: "select", options: store.costCenterOptions },
]);

const sources = computed<ItemSource[]>(() =>
  props.kind === "sales"
    ? [
        { label: "Sales Order", param: "sales_order_id", endpoint: "/sales-orders" },
        { label: "Delivery Note", param: "delivery_note_id", endpoint: "/delivery-notes" },
      ]
    : [
        { label: "Purchase Order", param: "purchase_order_id", endpoint: "/purchase-orders" },
        { label: "Purchase Receipt", param: "purchase_receipt_id", endpoint: "/purchase-receipts" },
      ],
);

const gridRows = computed<Record<string, unknown>[]>({
  get: () => items.value as unknown as Record<string, unknown>[],
  set: (rows) => {
    items.value = rows as unknown as InvoiceItemIn[];
  },
});

function newItemRow(): Record<string, unknown> {
  return {
    item_id: "", item_name: "", qty: 1, rate: 0, uom: "",
    discount_percentage: 0, hsn_sac_code: "", account_id: null, cost_center_id: null, _rowKey: rowKey(),
  };
}

async function onItemChange(index: number): Promise<void> {
  const row = items.value[index];
  if (!row?.item_id) return;
  const item = stock.items.find((it) => it.id === row.item_id);
  // default to the buy/sell UOM; rate is per that UOM (resolved rate is per stock UOM)
  const uom = (props.kind === "purchase" ? item?.purchase_uom : item?.sales_uom) || item?.stock_uom || "";
  const factor = stock.uomFactor(row.item_id, uom);
  let rate = row.rate;
  try {
    const resolved = await stock.resolveItemRate(row.item_id, props.kind === "purchase");
    rate = Number(resolved.rate) * factor;
  } catch {
    // best-effort; backend re-resolves on save
  }
  items.value = items.value.map((r, i) =>
    i === index
      ? {
          ...r,
          item_name: item?.item_name ?? r.item_name,
          item_code: item?.item_code ?? r.item_code,
          // snapshot HSN/SAC from the master, but keep a manual override the user already typed
          hsn_sac_code: r.hsn_sac_code || item?.hsn_sac_code || null,
          uom,
          rate,
        }
      : r,
  );
}

function onGetItems(param: string, id: string): void {
  void router.push({ name: meta.value.newRoute, query: { [param]: id } });
}

// Import (CSV / Tally) → append rows. Invoices allow free-text lines, so an
// unknown item_code still imports (as its own name).
function applyImportedRows(rows: ImportedRow[]): void {
  const additions: InvoiceItemIn[] = [];
  for (const r of rows) {
    const item = stock.items.find((it) => it.item_code.toLowerCase() === r.item_code.toLowerCase());
    additions.push({
      item_id: item?.id ?? null,
      item_code: item?.item_code ?? r.item_code,
      item_name: item?.item_name ?? r.item_code,
      qty: Number(r.qty) || 1,
      rate: r.rate != null ? Number(r.rate) : Number(item?.standard_rate ?? 0) || 0,
      uom: item?.stock_uom,
      _rowKey: rowKey(),
    });
  }
  // keep real or in-progress lines; drop only blank placeholders
  if (additions.length) {
    items.value = [...items.value.filter((i) => i.item_name || i.item_id || Number(i.rate)), ...additions];
  }
}

async function quickCreateParty(): Promise<void> {
  if (!newPartyName.value) return;
  error.value = null;
  try {
    const created =
      props.kind === "sales"
        ? await store.createCustomer(newPartyName.value)
        : await store.createSupplier(newPartyName.value);
    partyId.value = created.id;
    newPartyName.value = "";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

function goToPayment(): void {
  if (!doc.value) return;
  void router.push({
    name: "payment-entries",
    query: {
      type: props.kind === "sales" ? "Receive" : "Pay",
      party_id:
        props.kind === "sales"
          ? (doc.value as SalesInvoiceDetail).customer_id
          : (doc.value as PurchaseInvoiceDetail).supplier_id,
    },
  });
}

// Buying a prepaid service (e.g. support hours)? Spin up a Service Credit
// pre-filled from this Purchase Invoice (supplier + first item line + the link).
function createServiceCredit(): void {
  if (!doc.value) return;
  const pi = doc.value as PurchaseInvoiceDetail;
  const line = pi.items.find((it) => (it as { item_id?: string | null }).item_id) ?? pi.items[0];
  const query: Record<string, string> = { purchase_invoice_id: pi.id, supplier_id: pi.supplier_id };
  if (line) {
    const itemId = (line as { item_id?: string | null }).item_id;
    if (itemId) query.item_id = itemId;
    if (line.qty != null) query.qty = String(line.qty);
    if (line.rate != null) query.rate = String(line.rate);
  }
  void router.push({ name: "service-credit-new", query });
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const payload: Record<string, unknown> = {
      posting_date: postingDate.value,
      due_date: dueDate.value || null,
      items: items.value.filter((i) => i.item_name),
      taxes: taxes.value.filter((t) => t.account_head_id),
      currency: currencyModel.value.currency || null,
      conversion_rate: currencyModel.value.conversion_rate || 1,
      apply_discount_on: discount.value.apply_discount_on,
      additional_discount_percentage: discount.value.additional_discount_percentage || 0,
      discount_amount: discount.value.discount_amount || 0,
      payment_terms_template_id: paymentTermsTemplateId.value || null,
      is_return: isReturn.value,
      return_against_id: isReturn.value ? returnAgainstId.value || null : null,
      place_of_supply: placeOfSupply.value || null,
      is_reverse_charge: isReverseCharge.value,
    };
    if (props.kind === "sales") {
      payload.customer_id = partyId.value;
      payload.debit_to_id = controlAccountId.value || null;
      payload.po_no = poNo.value || null;
      payload.po_date = poDate.value || null;
      payload.terms = terms.value || null;
      Object.assign(payload, acPayload("customer"));
    } else {
      payload.supplier_id = partyId.value;
      payload.credit_to_id = controlAccountId.value || null;
      payload.terms = terms.value || null;
      payload.bill_no = billNo.value || null;
      payload.bill_date = billDate.value || null;
      payload.tax_withholding_category_id = tdsCategoryId.value || null;
      Object.assign(payload, acPayload("supplier"));
    }
    const resp = await api.post<InvoiceDetail>(endpoint.value, payload);
    void router.push(`${endpoint.value}/${resp.data.id}`);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function action(name: "submit" | "cancel"): Promise<void> {
  if (!doc.value) return;
  error.value = null;
  try {
    doc.value = await store.docAction<InvoiceDetail>(endpoint.value, doc.value.id, name);
    await loadAdvances();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

// India GST e-documents: download the e-invoice / e-way-bill JSON for a submitted
// sales invoice (gated per-company by GST Settings; a 422 explains if it's off).
async function downloadEdoc(kind: "e-invoice" | "e-way-bill"): Promise<void> {
  if (!doc.value) return;
  error.value = null;
  try {
    const data = (
      await api.get(`/e-documents/sales-invoices/${doc.value.id}/${kind}`)
    ).data;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${kind}_${doc.value.name}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

// --- Advances: apply a party's on-account payments to this invoice -----------
// Reuses Payment Reconciliation (an on-account receipt already sits as a credit
// on the party's receivable/payable; allocating just links it and drops the
// outstanding — no new GL).
const advances = ref<UnreconciledPaymentRow[]>([]);
const advanceAlloc = ref<Record<string, number>>({});
const applyingAdvance = ref(false);

const docPartyType = computed(() => (props.kind === "sales" ? "Customer" : "Supplier"));
const docPartyId = computed(() =>
  props.kind === "sales"
    ? (doc.value as SalesInvoiceDetail | null)?.customer_id
    : (doc.value as PurchaseInvoiceDetail | null)?.supplier_id,
);

async function loadAdvances(): Promise<void> {
  advances.value = [];
  advanceAlloc.value = {};
  if (!doc.value || doc.value.docstatus !== 1 || Number(doc.value.outstanding_amount) <= 0) return;
  const partyId = docPartyId.value;
  if (!partyId) return;
  try {
    const resp = await api.get<UnreconciledResponse>("/payment-reconciliation/unreconciled", {
      params: { party_type: docPartyType.value, party_id: partyId },
    });
    advances.value = resp.data.payments;
    let remaining = Number(doc.value.outstanding_amount);
    for (const p of advances.value) {
      const suggested = Math.min(Number(p.unallocated_amount), remaining);
      advanceAlloc.value[p.payment_entry_id] = suggested > 0 ? suggested : 0;
      remaining -= suggested > 0 ? suggested : 0;
    }
  } catch {
    advances.value = [];
  }
}

async function applyAdvances(): Promise<void> {
  if (!doc.value) return;
  const partyId = docPartyId.value;
  const allocations = advances.value
    .filter((p) => Number(advanceAlloc.value[p.payment_entry_id]) > 0)
    .map((p) => ({
      payment_entry_id: p.payment_entry_id,
      invoice_type: props.kind === "sales" ? "Sales Invoice" : "Purchase Invoice",
      invoice_id: doc.value!.id,
      allocated_amount: Number(advanceAlloc.value[p.payment_entry_id]),
    }));
  if (!allocations.length || !partyId) return;
  applyingAdvance.value = true;
  error.value = null;
  try {
    await api.post("/payment-reconciliation/reconcile", {
      party_type: docPartyType.value,
      party_id: partyId,
      allocations,
    });
    doc.value = (await api.get<InvoiceDetail>(`${endpoint.value}/${props.id}`)).data;
    await loadAdvances();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    applyingAdvance.value = false;
  }
}

const totalAdvanceToApply = computed(() =>
  Object.values(advanceAlloc.value).reduce((s, v) => s + (Number(v) || 0), 0),
);

interface SourceItem {
  id: string;
  item_id: string | null;
  item_code: string | null;
  item_name: string | null;
  qty: string;
  rate: string;
  billed_qty?: string;
  billed_amt?: string;
  delivered_qty?: string;
  received_qty?: string;
  sales_order_item_id?: string | null;
  purchase_order_item_id?: string | null;
}

// "Create Invoice" from an order / delivery / receipt prefills the unbilled rows
async function prefillFromSource(): Promise<void> {
  const sources: Array<{
    param: string;
    endpoint: string;
    partyKey: "customer_id" | "supplier_id";
    link: (row: SourceItem) => Partial<InvoiceItemIn>;
    unbilled: (row: SourceItem) => number;
  }> = props.kind === "sales"
    ? [
        {
          param: "sales_order_id", endpoint: "/sales-orders", partyKey: "customer_id",
          link: (row) => ({ sales_order_item_id: row.id }),
          unbilled: (row) =>
            Number(row.rate) > 0
              ? Number(row.qty) - (Number(row.billed_amt) || 0) / Number(row.rate)
              : Number(row.qty),
        },
        {
          param: "delivery_note_id", endpoint: "/delivery-notes", partyKey: "customer_id",
          link: (row) => ({
            delivery_note_item_id: row.id,
            sales_order_item_id: row.sales_order_item_id ?? null,
          }),
          unbilled: (row) => Number(row.qty) - (Number(row.billed_qty) || 0),
        },
      ]
    : [
        {
          param: "purchase_order_id", endpoint: "/purchase-orders", partyKey: "supplier_id",
          link: (row) => ({ purchase_order_item_id: row.id }),
          unbilled: (row) =>
            Number(row.rate) > 0
              ? Number(row.qty) - (Number(row.billed_amt) || 0) / Number(row.rate)
              : Number(row.qty),
        },
        {
          param: "purchase_receipt_id", endpoint: "/purchase-receipts", partyKey: "supplier_id",
          link: (row) => ({
            purchase_receipt_item_id: row.id,
            purchase_order_item_id: row.purchase_order_item_id ?? null,
          }),
          unbilled: (row) => Number(row.qty) - (Number(row.billed_qty) || 0),
        },
      ];

  for (const source of sources) {
    const docId = route.query[source.param];
    if (typeof docId !== "string") continue;
    const sourceDoc = (
      await api.get<Record<string, unknown> & { items: SourceItem[] }>(`${source.endpoint}/${docId}`)
    ).data;
    partyId.value = String(sourceDoc[source.partyKey] ?? "");
    items.value = sourceDoc.items
      .map((row) => ({ row, pending: source.unbilled(row) }))
      .filter(({ pending }) => pending > 0.000001)
      .map(({ row, pending }) => ({
        item_name: row.item_name ?? row.item_code ?? "",
        item_code: row.item_code,
        qty: Math.round(pending * 1000) / 1000,
        rate: Number(row.rate),
        item_id: row.item_id,
        ...source.link(row),
        _rowKey: rowKey(),
      }));
    return;
  }
}

// On a purchase Debit Note, seed the Supplier Invoice No./Date from the invoice
// being returned against (the dropdown options only carry id+name, so we fetch
// the original by id). ERPNext copies bill_no (no_copy=0) but deliberately
// leaves bill_date blank (no_copy=1); we seed both by request. Per-field empty
// guard so anything the user already typed is never overwritten.
async function onReturnAgainstChange(): Promise<void> {
  if (props.kind !== "purchase" || !returnAgainstId.value) return;
  if (billNo.value && billDate.value) return; // nothing to fill
  try {
    const orig = (
      await api.get<PurchaseInvoiceDetail>(`${endpoint.value}/${returnAgainstId.value}`)
    ).data;
    if (!billNo.value) billNo.value = orig.bill_no ?? "";
    if (!billDate.value) billDate.value = orig.bill_date ?? "";
  } catch {
    // non-fatal — the user can still type the fields manually
  }
}

// "Get Items From" pushes this same /new path with only a source id added to the
// query, so the prefill has to re-run on a query change rather than on mount only.
watch(
  () => [
    route.query.sales_order_id,
    route.query.delivery_note_id,
    route.query.purchase_order_id,
    route.query.purchase_receipt_id,
  ],
  () => {
    if (!props.id) void prefillFromSource();
  },
);

onMounted(async () => {
  await Promise.all([
    store.fetchAccounts(),
    store.fetchTaxTemplates(props.kind),
    store.fetchCostCenters(),
    fetchTermsTemplates(),
    fetchPaymentTermsTemplates(),
    stock.fetchItems(),
    props.kind === "sales" ? store.fetchCustomers() : store.fetchSuppliers(),
  ]);
  try {
    // purchase invoices withhold TDS; sales invoices collect TCS
    const wantKind = props.kind === "sales" ? "TCS" : "TDS";
    const r = await api.get<ListResponse<{ id: string; category_name: string; rate: string; kind: string; disabled: boolean }>>(
      "/registry/tax-withholding-category",
      { params: { page_size: 200 } },
    );
    tdsCategories.value = (r.data.items ?? []).filter((c) => c.kind === wantKind && !c.disabled);
  } catch {
    tdsCategories.value = [];
  }
  if (props.id) {
    doc.value = (await api.get<InvoiceDetail>(`${endpoint.value}/${props.id}`)).data;
    await loadAdvances();
  } else {
    await prefillFromSource();
    try {
      const resp = await api.get<ListResponse<InvoiceListItem>>(endpoint.value, {
        params: { page_size: 50 },
      });
      returnables.value = resp.data.items.filter((i) => i.docstatus === 1);
    } catch {
      // non-fatal — the return-against picker just stays empty
    }
  }
});
</script>

<template>
  <div>
    <!-- existing invoice: detail + actions -->
    <div v-if="doc" class="max-w-5xl">
      <div class="mb-4 flex items-center justify-between">
        <div>
          <h1 class="text-xl font-semibold text-gray-900">{{ docPartyName || doc.name }}</h1>
          <p class="text-sm text-gray-500">{{ doc.name }}</p>
        </div>
        <div class="flex items-center gap-3">
          <StatusBadge :status="doc.status" />
          <button v-if="doc.docstatus === 0" class="btn-primary" @click="action('submit')">Submit</button>
          <button
            v-if="doc.docstatus === 1 && Number(doc.outstanding_amount) > 0"
            class="btn-primary"
            @click="goToPayment"
          >{{ kind === "sales" ? "Receive Payment" : "Pay" }}</button>
          <button
            v-if="kind === 'purchase' && doc.docstatus === 1"
            class="btn-secondary"
            @click="createServiceCredit"
          >Create Service Credit</button>
          <button v-if="doc.docstatus === 1" class="btn-secondary" @click="action('cancel')">Cancel</button>
          <button
            v-if="kind === 'sales' && doc.docstatus === 1"
            class="btn-secondary"
            title="Download the NIC e-invoice JSON (needs E-Invoice enabled in GST Settings)"
            @click="downloadEdoc('e-invoice')"
          >e-Invoice JSON</button>
          <button
            v-if="kind === 'sales' && doc.docstatus === 1"
            class="btn-secondary"
            title="Download the e-way-bill JSON (needs E-Way Bill enabled in GST Settings)"
            @click="downloadEdoc('e-way-bill')"
          >e-Way Bill JSON</button>
          <PrintButton :path="`${endpoint}/${doc.id}/pdf`" :title="`${doc.name} — Preview`" />
          <SendEmailButton :doctype="meta.title" :doc-id="doc.id" :doc-name="doc.name" />
        </div>
      </div>
      <p v-if="error" class="mb-3 text-sm text-red-600">{{ error.detail }}</p>

      <div class="mb-4 grid grid-cols-2 gap-x-8 gap-y-3 rounded-lg border border-gray-200 bg-white p-5 shadow-sm md:grid-cols-4">
        <div>
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">{{ partyLabel }}</div>
          <div class="mt-0.5 text-sm font-medium text-gray-900">{{ docPartyName || "—" }}</div>
        </div>
        <div>
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Posting Date</div>
          <div class="mt-0.5 text-sm text-gray-900">{{ formatDate(doc.posting_date) }}</div>
        </div>
        <div>
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Due Date</div>
          <div class="mt-0.5 text-sm text-gray-900">{{ formatDate(doc.due_date) }}</div>
        </div>
        <div>
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Currency</div>
          <div class="mt-0.5 text-sm text-gray-900">{{ doc.currency }}</div>
        </div>
        <div v-if="docBillNo">
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Supplier Invoice</div>
          <div class="mt-0.5 text-sm text-gray-900">{{ docBillNo }}</div>
        </div>
        <div v-if="doc.po_no">
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Customer PO</div>
          <div class="mt-0.5 text-sm text-gray-900">
            {{ doc.po_no }}<span v-if="doc.po_date" class="text-gray-500"> · {{ formatDate(doc.po_date) }}</span>
          </div>
        </div>
        <div v-if="doc.remarks" class="col-span-2 md:col-span-3">
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Remarks</div>
          <div class="mt-0.5 text-sm text-gray-700">{{ doc.remarks }}</div>
        </div>
        <div v-if="paymentTermsName">
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Payment Terms</div>
          <div class="mt-0.5 text-sm text-gray-900">{{ paymentTermsName }}</div>
        </div>
        <div v-if="doc.terms" class="col-span-2 md:col-span-4">
          <div class="text-xs font-semibold uppercase tracking-wide text-gray-400">Terms</div>
          <div class="mt-0.5 whitespace-pre-line text-sm text-gray-700">{{ doc.terms }}</div>
        </div>
        <AddressContactSummary
          :billing-address-id="doc.customer_address_id ?? doc.supplier_address_id"
          :shipping-address-id="doc.shipping_address_id"
          :contact-person-id="doc.contact_person_id"
          class="col-span-2 md:col-span-4"
        />
      </div>

      <div class="rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
            <tr><th class="px-4 py-2">#</th><th class="px-4 py-2">Item</th>
                <th class="px-4 py-2 text-right">Qty</th><th class="px-4 py-2 text-right">Rate</th>
                <th v-if="hasLineDiscount" class="px-4 py-2 text-right">Disc %</th>
                <th class="px-4 py-2 text-right">Amount</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in doc.items" :key="item.idx" class="border-t border-gray-100">
              <td class="px-4 py-2">{{ item.idx }}</td>
              <td class="px-4 py-2 font-medium text-gray-900">{{ item.item_name }}</td>
              <td class="px-4 py-2 text-right">{{ formatQty(item.qty) }}</td>
              <td class="px-4 py-2 text-right">{{ money(Number(item.price_list_rate) || item.rate) }}</td>
              <td v-if="hasLineDiscount" class="px-4 py-2 text-right">
                {{ Number(item.discount_percentage) ? `${Number(item.discount_percentage)}%` : "—" }}
              </td>
              <td class="px-4 py-2 text-right">{{ money(item.amount) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="border-t border-gray-200 p-4">
          <dl class="ml-auto w-80 space-y-1 text-sm">
            <div class="flex justify-between"><dt class="text-gray-500">Net Total</dt><dd>{{ money(doc.net_total) }}</dd></div>
            <div v-for="tax in doc.taxes" :key="tax.idx" class="flex justify-between">
              <dt class="text-gray-500">{{ taxLabel(tax) }} <template v-if="taxEffectiveRate(tax) !== null">({{ taxEffectiveRate(tax) }}%)</template></dt>
              <dd>{{ money(tax.tax_amount) }}</dd>
            </div>
            <div v-if="Number(doc.discount_amount)" class="flex justify-between text-gray-500">
              <dt>Discount</dt><dd>-{{ money(doc.discount_amount) }}</dd>
            </div>
            <div class="flex justify-between border-t border-gray-200 pt-1 text-base font-semibold">
              <dt>Grand Total</dt><dd>{{ money(doc.rounded_total) }}</dd>
            </div>
            <div v-if="Number(doc.tax_withholding_amount) > 0" class="flex justify-between text-gray-500">
              <template v-if="kind === 'sales'">
                <dt>TCS collected</dt><dd>+{{ formatCurrency(doc.tax_withholding_amount, companyCurrency) }}</dd>
              </template>
              <template v-else>
                <dt>TDS withheld</dt><dd>-{{ formatCurrency(doc.tax_withholding_amount, companyCurrency) }}</dd>
              </template>
            </div>
            <!-- outstanding is tracked in company (base) currency -->
            <div class="flex justify-between" :class="Number(doc.outstanding_amount) > 0 ? 'font-medium text-red-700' : 'text-gray-500'">
              <dt>Outstanding</dt><dd>{{ formatCurrency(doc.outstanding_amount, companyCurrency) }}</dd>
            </div>
          </dl>
          <PaymentSchedulePreview
            v-if="doc.payment_terms_template_id"
            :template-id="doc.payment_terms_template_id"
            :total="Number(doc.rounded_total)"
            :posting-date="doc.posting_date"
            :currency="doc.currency"
          />
        </div>
      </div>

      <!-- Advances: apply the party's on-account payments to this invoice -->
      <div
        v-if="advances.length && Number(doc.outstanding_amount) > 0"
        class="mt-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      >
        <h2 class="text-sm font-semibold text-gray-900">Advances available</h2>
        <p class="mb-3 text-xs text-gray-500">
          On-account payments from this {{ partyLabel.toLowerCase() }} — allocate them to this invoice to reduce its outstanding.
        </p>
        <div v-for="p in advances" :key="p.payment_entry_id" class="mb-2 flex flex-wrap items-center gap-3 text-sm">
          <span class="w-44 font-medium text-gray-800">{{ p.name }}</span>
          <span class="w-24 text-gray-500">{{ formatDate(p.posting_date) }}</span>
          <span class="w-40 text-gray-500">Unallocated: {{ money(p.unallocated_amount) }}</span>
          <input
            v-model.number="advanceAlloc[p.payment_entry_id]"
            type="number" min="0" step="any"
            class="form-input w-32 text-right"
          />
        </div>
        <div class="mt-3 flex items-center justify-end gap-3 border-t border-gray-100 pt-3">
          <span class="text-sm text-gray-600">Applying: <strong>{{ money(totalAdvanceToApply) }}</strong></span>
          <button class="btn-primary" :disabled="applyingAdvance || totalAdvanceToApply <= 0" @click="applyAdvances">
            {{ applyingAdvance ? "Applying…" : "Apply Advances" }}
          </button>
        </div>
      </div>
    </div>

    <!-- new invoice form (ERPNext-style) -->
    <form v-else @submit.prevent="save">
      <!-- top bar: breadcrumb + actions -->
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <nav class="flex flex-wrap items-center gap-2 text-sm text-gray-500">
          <span>{{ meta.module }}</span><span class="text-gray-300">/</span>
          <span>{{ meta.title }}</span><span class="text-gray-300">/</span>
          <span class="font-semibold text-gray-900">New {{ meta.title }}</span>
          <span class="ml-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
            Not Saved
          </span>
        </nav>
        <div class="flex items-center gap-2">
          <GetItemsFrom :sources="sources" @select="onGetItems" />
          <button type="submit" class="btn-primary" :disabled="saving || !partyId">
            {{ saving ? "Saving…" : "Save" }}
          </button>
        </div>
      </div>

      <!-- tabs -->
      <div class="mb-6 border-b border-gray-200">
        <nav class="flex gap-6">
          <button
            v-for="tab in tabs"
            :key="tab"
            type="button"
            class="-mb-px border-b-2 px-1 pb-2 text-sm font-medium"
            :class="activeTab === tab ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
            @click="activeTab = tab"
          >
            {{ tab }}
          </button>
        </nav>
      </div>

      <div v-show="activeTab === 'Details'" class="space-y-8">
        <!-- header fields -->
        <div class="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-3">
          <div>
            <label class="form-label">Series</label>
            <div class="form-input bg-gray-50 text-gray-600">{{ meta.series }}</div>
          </div>
          <div>
            <label class="form-label">Posting Date <span class="text-red-500">*</span></label>
            <DateField v-model="postingDate" required />
          </div>
          <div>
            <label class="form-label">Payment Due Date</label>
            <DateField v-model="dueDate" />
          </div>
          <div>
            <label class="form-label">{{ partyLabel }} <span class="text-red-500">*</span></label>
            <select v-model="partyId" required class="form-input">
              <option value="" disabled>Select…</option>
              <option v-for="p in parties" :key="p.id" :value="p.id">
                {{ "customer_name" in p ? p.customer_name : p.supplier_name }}
              </option>
            </select>
            <div class="mt-1 flex gap-2">
              <input v-model="newPartyName" class="form-input" :placeholder="`Quick add ${partyLabel.toLowerCase()}…`" />
              <button type="button" class="btn-secondary" @click="quickCreateParty">Add</button>
            </div>
          </div>
          <div>
            <label class="form-label">{{ controlAccountLabel }}</label>
            <select v-model="controlAccountId" class="form-input">
              <option value="">Company default…</option>
              <option v-for="a in controlAccountOptions" :key="a.value" :value="a.value">{{ a.label }}</option>
            </select>
          </div>
          <div>
            <label class="form-label">Company</label>
            <div class="form-input bg-gray-50 text-gray-600">{{ companyName || "—" }}</div>
          </div>
          <div>
            <label class="form-label">Place of Supply (GST)</label>
            <input v-model="placeOfSupply" class="form-input" placeholder="Auto from GSTIN (e.g. 27-Maharashtra)" />
          </div>
          <div class="flex items-end pb-2">
            <label class="flex items-center gap-2 text-sm text-gray-700">
              <input v-model="isReverseCharge" type="checkbox" class="rounded border-gray-300" />
              Reverse charge
            </label>
          </div>
          <div class="flex items-end pb-2">
            <label class="flex items-center gap-2 text-sm text-gray-700">
              <input v-model="isReturn" type="checkbox" class="rounded border-gray-300" />
              Is Return ({{ kind === "sales" ? "Credit Note" : "Debit Note" }})
            </label>
          </div>
          <div v-if="isReturn">
            <label class="form-label">Return Against</label>
            <select v-model="returnAgainstId" class="form-input" @change="onReturnAgainstChange">
              <option value="">Select original invoice…</option>
              <option v-for="inv in returnables" :key="inv.id" :value="inv.id">
                {{ inv.name }}<template v-if="inv.customer_name || inv.supplier_name"> — {{ inv.customer_name ?? inv.supplier_name }}</template>
              </option>
            </select>
          </div>
          <div v-if="kind === 'sales'">
            <label class="form-label">Customer's PO No.</label>
            <input v-model="poNo" class="form-input" placeholder="Customer PO reference" />
          </div>
          <div v-if="kind === 'sales'">
            <label class="form-label">Customer's PO Date</label>
            <DateField v-model="poDate" />
          </div>
          <div v-if="kind === 'purchase'">
            <label class="form-label">Supplier Invoice No.</label>
            <input v-model="billNo" class="form-input" placeholder="Supplier's invoice number" />
          </div>
          <div v-if="kind === 'purchase'">
            <label class="form-label">Supplier Invoice Date</label>
            <DateField v-model="billDate" />
          </div>
        </div>

        <!-- items -->
        <div>
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900">Items &amp; Services</h2>
            <DataEntry @import="applyImportedRows" />
          </div>
          <ItemsGrid
            v-model="gridRows"
            :columns="gridColumns"
            :item-options="stock.itemOptions"
            :currency="currencyModel.currency || companyCurrency"
            :new-row="newItemRow"
            @item-change="onItemChange"
          />
        </div>

        <!-- currency -->
        <CurrencySection v-model="currencyModel" :company-currency="companyCurrency" />

        <!-- taxes & charges -->
        <div v-if="store.taxTemplates.length" class="max-w-sm">
          <label class="form-label">Taxes and Charges Template</label>
          <select v-model="taxTemplateId" class="form-input" @change="applyTaxTemplate">
            <option value="">— Choose a template to fill the rows —</option>
            <option v-for="t in store.taxTemplates" :key="t.id" :value="t.id">{{ t.title }}</option>
          </select>
        </div>
        <TaxesCharges v-model="taxes" :account-options="store.accountOptions" />

        <!-- Auto GST preview (sales): what the server will charge from each line's
             HSN / Item Tax Template. Shown until the user fills the rows manually. -->
        <div
          v-if="!taxes.length && previewTaxes.length"
          class="mt-2 max-w-sm rounded-md border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs text-emerald-800"
        >
          <div class="mb-1 font-medium">GST (auto, from item HSN)</div>
          <div v-for="(t, i) in previewTaxes" :key="i" class="flex justify-between">
            <span>{{ t.description }}<span v-if="Number(t.rate) > 0"> @ {{ Number(t.rate) }}%</span></span>
            <span class="tabular-nums">{{ formatCurrency(String(t.tax_amount ?? 0), currencyModel.currency || companyCurrency) }}</span>
          </div>
        </div>

        <!-- India TDS (purchase) / TCS (sales) -->
        <div v-if="tdsCategories.length" class="mt-3 max-w-sm">
          <label class="form-label">{{ kind === "sales" ? "TCS — Tax Collected at Source" : "TDS — Tax Withholding" }}</label>
          <select v-model="tdsCategoryId" class="form-input">
            <option value="">— none —</option>
            <option v-for="c in tdsCategories" :key="c.id" :value="c.id">
              {{ c.category_name }} ({{ Number(c.rate) }}%)
            </option>
          </select>
          <p class="mt-1 text-xs text-gray-400">
            {{ kind === "sales"
              ? "Collected from the customer on top of the invoice and credited to the TCS payable account."
              : "Withheld from the supplier payment and credited to the TDS payable account." }}
          </p>
        </div>

        <!-- additional discount -->
        <AdditionalDiscount v-model="discount" />

        <!-- totals -->
        <DocumentTotals
          :items="items"
          :taxes="effectiveTaxes"
          :discount="discount"
          :currency="currencyModel.currency || companyCurrency"
          :withholding="withholdingPreview"
        />

        <p v-if="error" class="text-sm text-red-600">
          {{ error.detail }}<span v-if="error.field" class="text-gray-400"> ({{ error.field }})</span>
        </p>
        <div class="flex justify-end">
          <button type="button" class="btn-secondary" @click="router.back()">Cancel</button>
        </div>
      </div>

      <div v-show="activeTab === 'Payments'" class="space-y-4 rounded-lg border border-gray-200 bg-white p-6 text-sm">
        <label class="flex items-center gap-2 text-gray-400">
          <input type="checkbox" disabled class="rounded border-gray-300" />
          Include Payment (POS) — coming soon
        </label>
        <p class="text-gray-600">
          For a standard invoice, record the payment after saving: open the submitted invoice and use
          <span class="font-medium text-gray-800">Receive Payment</span>, which creates a Payment Entry
          allocated against it. Point-of-sale payment capture on the invoice itself isn’t enabled yet.
        </p>
      </div>
      <div v-show="activeTab === 'Address & Contact'">
        <AddressContactTab
          v-model="addressContact"
          :party-id="partyId"
          :party-kind="kind === 'sales' ? 'customer' : 'supplier'"
        />
      </div>
      <div v-show="activeTab === 'Terms'" class="space-y-4">
        <div v-if="termsTemplates.length" class="max-w-sm">
          <label class="form-label">Terms Template</label>
          <select v-model="termsTemplateId" class="form-input" @change="applyTermsTemplate">
            <option value="">— Choose a template to fill the text —</option>
            <option v-for="t in termsTemplates" :key="t.id" :value="t.id">{{ t.template_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Terms and Conditions</label>
          <textarea
            v-model="terms"
            rows="6"
            class="form-input"
            placeholder="Payment terms, delivery terms, warranty…"
          ></textarea>
        </div>

        <!-- Payment Terms: pick a template, see when each part is due -->
        <div class="border-t border-gray-100 pt-4">
          <div class="max-w-sm">
            <label class="form-label">Payment Terms</label>
            <select v-model="paymentTermsTemplateId" class="form-input">
              <option value="">— None (pay in full) —</option>
              <option v-for="t in paymentTermsTemplates" :key="t.id" :value="t.id">{{ t.template_name }}</option>
            </select>
          </div>
          <PaymentSchedulePreview
            :template-id="paymentTermsTemplateId"
            :total="liveGrandTotal"
            :posting-date="postingDate"
            :currency="currencyModel.currency || companyCurrency"
          />
        </div>
      </div>
      <div
        v-show="
          activeTab !== 'Details' &&
          activeTab !== 'Payments' &&
          activeTab !== 'Terms' &&
          activeTab !== 'Address & Contact'
        "
        class="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center text-sm text-gray-400"
      >
        This section isn’t built yet.
      </div>
    </form>
  </div>
</template>
