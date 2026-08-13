<script setup lang="ts">
// Payment Entry list + creation (Receive/Pay with invoice allocation,
// on-account advances supported via an independent amount field).

import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import PaginationFooter from "@/components/shared/PaginationFooter.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import { useList } from "@/composables/useList";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { useAccountsStore } from "@/stores/accounts";
import { api } from "@/api/client";
import { formatCurrency, formatDate } from "@/utils/format";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { InvoiceListItem, PaymentEntryListItem } from "@/types/accounts";

const store = useAccountsStore();
const route = useRoute();
const router = useRouter();
const openDetail = (row: PaymentEntryListItem): void => void router.push(`/payment-entries/${row.id}`);
const companyCurrency = useCompanyCurrency();
const { items, total, page, pageSize, loading, fetchList, goToPage } =
  useList<PaymentEntryListItem>("/payment-entries");

const showForm = ref(false);
const paymentType = ref<"Receive" | "Pay">("Receive");
const partyId = ref("");
const bankAccountId = ref("");
const postingDate = ref(new Date().toISOString().slice(0, 10));
const paidAmount = ref<number | null>(null); // null = follow allocations
const openInvoices = ref<InvoiceListItem[]>([]);
const allocations = ref<Record<string, number>>({});
// GST on advances: a Receive with an unallocated remainder can carry output GST if it is a
// service advance (a bare advance has no HSN, so the rate is explicit).
const advanceSupplyType = ref<"Goods" | "Services">("Goods");
const advanceGstRate = ref<number | null>(null);
const error = ref<ErrorEnvelope | null>(null);
const saving = ref(false);

const parties = computed(() => (paymentType.value === "Receive" ? store.customers : store.suppliers));
const invoiceEndpoint = computed(() =>
  paymentType.value === "Receive" ? "/sales-invoices" : "/purchase-invoices",
);
const refDoctype = computed(() =>
  paymentType.value === "Receive" ? "Sales Invoice" : "Purchase Invoice",
);
const bankAccounts = computed(() =>
  store.leafAccounts.filter((a) => a.account_type === "Bank" || a.account_type === "Cash"),
);
const totalAllocated = computed(() =>
  Object.values(allocations.value).reduce((s, v) => s + (Number(v) || 0), 0),
);
const effectiveAmount = computed(() =>
  paidAmount.value !== null && paidAmount.value > 0 ? paidAmount.value : totalAllocated.value,
);
const unallocatedPreview = computed(() => effectiveAmount.value - totalAllocated.value);
const amountTooLow = computed(() => totalAllocated.value > effectiveAmount.value + 0.005);

watch(paymentType, () => {
  partyId.value = "";
  openInvoices.value = [];
  allocations.value = {};
});

watch(partyId, async () => {
  openInvoices.value = [];
  allocations.value = {};
  if (!partyId.value) return;
  // submitted invoices with an outstanding amount, for the selected party only
  const partyParam =
    paymentType.value === "Receive"
      ? { customer_id: partyId.value }
      : { supplier_id: partyId.value };
  const resp = await api.get<ListResponse<InvoiceListItem>>(invoiceEndpoint.value, {
    params: { page_size: 200, ...partyParam },
  });
  openInvoices.value = resp.data.items.filter(
    (i) => i.docstatus === 1 && Number(i.outstanding_amount) > 0,
  );
});

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const references = Object.entries(allocations.value)
      .filter(([, amount]) => Number(amount) > 0)
      .map(([referenceId, amount]) => ({
        reference_doctype: refDoctype.value,
        reference_id: referenceId,
        allocated_amount: Number(amount),
      }));
    const payload: Record<string, unknown> = {
      posting_date: postingDate.value,
      payment_type: paymentType.value,
      party_type: paymentType.value === "Receive" ? "Customer" : "Supplier",
      party_id: partyId.value,
      paid_amount: effectiveAmount.value,
      references,
    };
    if (paymentType.value === "Receive") {
      payload.paid_to_id = bankAccountId.value;
      // book GST on a service advance (only when there's an unallocated remainder)
      if (advanceSupplyType.value === "Services" && unallocatedPreview.value > 0.005 && advanceGstRate.value) {
        payload.advance_supply_type = "Services";
        payload.advance_gst_rate = advanceGstRate.value;
      }
    } else payload.paid_from_id = bankAccountId.value;

    const resp = await api.post("/payment-entries", payload);
    await api.post(`/payment-entries/${(resp.data as { id: string }).id}/submit`);
    showForm.value = false;
    allocations.value = {};
    paidAmount.value = null;
    advanceSupplyType.value = "Goods";
    advanceGstRate.value = null;
    await fetchList();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

const columns: Column[] = [
  { key: "name", label: "Payment" },
  { key: "party", label: "Party" },
  { key: "posting_date", label: "Date" },
  { key: "payment_type", label: "Type" },
  { key: "reference_no", label: "Reference" },
  { key: "paid_amount", label: "Amount", class: "text-right" },
  { key: "unallocated_amount", label: "Unallocated", class: "text-right" },
  { key: "status", label: "Status" },
];

const partyNames = computed(() => {
  const map = new Map<string, string>();
  for (const c of store.customers) map.set(c.id, c.customer_name);
  for (const s of store.suppliers) map.set(s.id, s.supplier_name);
  return map;
});

function partyName(row: PaymentEntryListItem): string {
  if (!row.party_id) return row.payment_type === "Internal Transfer" ? "Internal" : "—";
  return partyNames.value.get(row.party_id) ?? "—";
}

// Deep link from an invoice's "Pay" button: ?type=Receive&party_id=...
// Applied reactively because the sidebar's bare /payment-entries link only drops
// the query without remounting the view, and must close the prefilled form.
function applyPrefillFromRoute(): void {
  const queryType = route.query.type;
  const queryParty = route.query.party_id;
  if (queryType !== "Receive" && queryType !== "Pay") {
    showForm.value = false;
    return;
  }
  paymentType.value = queryType;
  showForm.value = true;
  if (typeof queryParty === "string") partyId.value = queryParty;
}

watch(() => [route.query.type, route.query.party_id], applyPrefillFromRoute);

onMounted(async () => {
  await Promise.all([fetchList(), store.fetchAccounts(), store.fetchCustomers(), store.fetchSuppliers()]);
  applyPrefillFromRoute();
});
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Payment Entries</h1>
        <p class="text-sm text-gray-500">{{ total }} total</p>
      </div>
      <button class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Close" : "New Payment" }}
      </button>
    </div>

    <form v-if="showForm" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm" @submit.prevent="save">
      <div class="grid grid-cols-5 gap-4">
        <div>
          <label class="form-label">Type</label>
          <select v-model="paymentType" class="form-input">
            <option>Receive</option>
            <option>Pay</option>
          </select>
        </div>
        <div>
          <label class="form-label">{{ paymentType === "Receive" ? "Customer" : "Supplier" }}*</label>
          <select v-model="partyId" required class="form-input">
            <option value="" disabled>Select…</option>
            <option v-for="p in parties" :key="p.id" :value="p.id">
              {{ "customer_name" in p ? p.customer_name : p.supplier_name }}
            </option>
          </select>
        </div>
        <div>
          <label class="form-label">Bank/Cash Account*</label>
          <select v-model="bankAccountId" required class="form-input">
            <option value="" disabled>Select…</option>
            <option v-for="a in bankAccounts" :key="a.id" :value="a.id">{{ a.account_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Posting Date*</label>
          <input v-model="postingDate" type="date" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Amount</label>
          <input
            v-model.number="paidAmount"
            type="number"
            min="0"
            step="any"
            class="form-input"
            :placeholder="totalAllocated ? totalAllocated.toFixed(2) : 'e.g. advance'"
          />
        </div>
      </div>

      <div v-if="openInvoices.length" class="mt-4">
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Allocate against open invoices</h2>
        <div
          v-for="invoice in openInvoices"
          :key="invoice.id"
          class="mb-1 grid grid-cols-12 items-center gap-2 text-sm"
        >
          <span class="col-span-4 font-medium text-gray-900">{{ invoice.name }}</span>
          <span class="col-span-3 text-gray-500">{{ formatDate(invoice.posting_date) }}</span>
          <span class="col-span-2 text-right">{{ formatCurrency(invoice.outstanding_amount, companyCurrency) }}</span>
          <input
            v-model.number="allocations[invoice.id]"
            type="number"
            min="0"
            step="any"
            :max="Number(invoice.outstanding_amount)"
            placeholder="Allocate"
            class="form-input col-span-3"
          />
        </div>
      </div>
      <p v-else-if="partyId" class="mt-4 text-sm text-gray-500">
        No open invoices for this party — the payment will be recorded on account
        (reconcile it later from the Reconciliation page).
      </p>

      <div
        v-if="paymentType === 'Receive' && unallocatedPreview > 0.005"
        class="mt-4 rounded-md border border-gray-200 bg-gray-50 p-3"
      >
        <h2 class="mb-2 text-sm font-semibold text-gray-900">Advance ({{ formatCurrency(unallocatedPreview, companyCurrency) }} on account)</h2>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="form-label">Supply type</label>
            <select v-model="advanceSupplyType" class="form-input">
              <option value="Goods">Goods (no GST on advance)</option>
              <option value="Services">Services (GST on advance)</option>
            </select>
          </div>
          <div v-if="advanceSupplyType === 'Services'">
            <label class="form-label">GST rate %</label>
            <input v-model.number="advanceGstRate" type="number" min="0" step="0.01" placeholder="e.g. 18" class="form-input" />
          </div>
        </div>
        <p class="mt-2 text-xs text-gray-500">
          A service advance is treated as GST-inclusive — output GST is booked now and reported in
          GSTR-1 Table 11, then reversed when the advance is adjusted to an invoice.
        </p>
      </div>

      <div class="mt-3 flex items-center justify-between text-sm">
        <p :class="amountTooLow ? 'text-red-600' : 'text-gray-600'">
          Paying {{ formatCurrency(effectiveAmount, companyCurrency) }}
          <template v-if="totalAllocated > 0">
            · allocated {{ formatCurrency(totalAllocated, companyCurrency) }}
          </template>
          <template v-if="unallocatedPreview > 0.005">
            · {{ formatCurrency(unallocatedPreview, companyCurrency) }} on account
          </template>
          <template v-if="amountTooLow"> — amount is less than the allocations</template>
        </p>
        <button
          type="submit"
          class="btn-primary"
          :disabled="saving || effectiveAmount <= 0 || amountTooLow || !bankAccountId || !partyId"
        >
          {{ saving ? "Posting…" : "Save & Submit" }}
        </button>
      </div>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
    </form>

    <DataTable :columns="columns" :rows="items" :loading="loading" @row-click="openDetail">
      <template #cell-name="{ row }">
        <span class="font-medium text-primary">{{ row.name }}</span>
      </template>
      <template #cell-party="{ row }">
        {{ partyName(row) }}
      </template>
      <template #cell-posting_date="{ value }">
        {{ formatDate(String(value)) }}
      </template>
      <template #cell-reference_no="{ value }">
        {{ value ?? "—" }}
      </template>
      <template #cell-paid_amount="{ value }">
        {{ formatCurrency(String(value), companyCurrency) }}
      </template>
      <template #cell-unallocated_amount="{ row }">
        <span :class="Number(row.unallocated_amount) > 0 ? 'font-medium text-amber-700' : 'text-gray-400'">
          {{ formatCurrency(row.unallocated_amount ?? 0, companyCurrency) }}
        </span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="String(value)" />
      </template>
    </DataTable>
    <PaginationFooter :page="page" :page-size="pageSize" :total="total" @go-to="goToPage" />
  </div>
</template>
