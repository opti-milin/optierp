<script setup lang="ts">
// Lean procurement sourcing: RFQs and Supplier Quotations in two tabs.

import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import ItemSelect from "@/components/shared/ItemSelect.vue";
import PaginationFooter from "@/components/shared/PaginationFooter.vue";
import StatusBadge from "@/components/shared/StatusBadge.vue";
import PrintButton from "@/components/shared/PrintButton.vue";
import SendEmailButton from "@/components/shared/SendEmailButton.vue";
import { useList } from "@/composables/useList";
import { useAccountsStore } from "@/stores/accounts";
import { useStockStore } from "@/stores/stock";
import { useCompanyCurrency } from "@/composables/useCompanyCurrency";
import { api } from "@/api/client";
import { formatCurrency, formatDate } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { OrderListItem, RFQDetail, RFQListItem } from "@/types/trade";

const router = useRouter();
const accounts = useAccountsStore();
const stock = useStockStore();
const companyCurrency = useCompanyCurrency();

const tab = ref<"rfq" | "sq">("rfq");

const rfqList = useList<RFQListItem>("/rfqs");
const sqList = useList<OrderListItem>("/supplier-quotations");

const error = ref<ErrorEnvelope | null>(null);
const saving = ref(false);
const showForm = ref(false);

// RFQ form
const rfqDate = ref(new Date().toISOString().slice(0, 10));
const rfqScheduleDate = ref(""); // response due / required by
const rfqMessage = ref("");
const rfqRows = ref<Array<{ item_id: string; qty: number }>>([{ item_id: "", qty: 1 }]);
const rfqSupplierIds = ref<string[]>([]);

// Supplier Quotation form (optionally prefilled from a selected RFQ)
const sqSupplierId = ref("");
const sqDate = ref(new Date().toISOString().slice(0, 10));
const sqValidTill = ref("");
const sqCurrency = ref("");
const sqRfqId = ref<string | null>(null);
const sqRows = ref<Array<{ item_id: string; qty: number; rate: number; rfq_item_id?: string | null }>>([
  { item_id: "", qty: 1, rate: 0 },
]);

// RFQ detail panel
const selectedRfq = ref<RFQDetail | null>(null);

async function openRfq(row: RFQListItem): Promise<void> {
  error.value = null;
  selectedRfq.value = (await api.get<RFQDetail>(`/rfqs/${row.id}`)).data;
}

function recordQuoteFor(supplierId?: string): void {
  if (!selectedRfq.value) return;
  sqRfqId.value = selectedRfq.value.id;
  sqSupplierId.value =
    supplierId ??
    selectedRfq.value.suppliers.find((s) => s.quote_status === "Pending")?.supplier_id ??
    "";
  sqRows.value = selectedRfq.value.items.map((item) => ({
    item_id: item.item_id,
    qty: Number(item.qty),
    rate: 0,
    rfq_item_id: item.id,
  }));
  tab.value = "sq";
  showForm.value = true;
}

const rfqColumns: Column[] = [
  { key: "name", label: "RFQ" },
  { key: "posting_date", label: "Date" },
  { key: "status", label: "Status" },
];
const sqColumns: Column[] = [
  { key: "name", label: "Quotation" },
  { key: "supplier_name", label: "Supplier" },
  { key: "posting_date", label: "Date" },
  { key: "grand_total", label: "Total", class: "text-right" },
  { key: "status", label: "Status" },
];

async function saveRfq(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const resp = await api.post<{ id: string }>("/rfqs", {
      posting_date: rfqDate.value,
      schedule_date: rfqScheduleDate.value || null,
      message_for_supplier: rfqMessage.value || null,
      items: rfqRows.value.filter((r) => r.item_id),
      supplier_ids: rfqSupplierIds.value,
    });
    await api.post(`/rfqs/${resp.data.id}/submit`);
    showForm.value = false;
    rfqRows.value = [{ item_id: "", qty: 1 }];
    rfqSupplierIds.value = [];
    rfqScheduleDate.value = "";
    rfqMessage.value = "";
    await rfqList.fetchList();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function saveSq(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    const resp = await api.post<{ id: string }>("/supplier-quotations", {
      supplier_id: sqSupplierId.value,
      posting_date: sqDate.value,
      valid_till: sqValidTill.value || null,
      currency: sqCurrency.value || null,
      rfq_id: sqRfqId.value,
      items: sqRows.value.filter((r) => r.item_id),
    });
    await api.post(`/supplier-quotations/${resp.data.id}/submit`);
    showForm.value = false;
    sqRows.value = [{ item_id: "", qty: 1, rate: 0 }];
    sqRfqId.value = null;
    sqValidTill.value = "";
    sqCurrency.value = "";
    await sqList.fetchList();
    // the supplier's quote status on the RFQ just flipped to Received
    if (selectedRfq.value) {
      selectedRfq.value = (await api.get<RFQDetail>(`/rfqs/${selectedRfq.value.id}`)).data;
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

function makePo(row: OrderListItem): void {
  // prefill a PO from this supplier's quoted rates
  void router.push({ name: "purchase-order-new", query: { supplier_quotation_id: row.id } });
}

onMounted(async () => {
  await Promise.all([
    rfqList.fetchList(),
    sqList.fetchList(),
    accounts.fetchSuppliers(),
    stock.fetchItems(),
  ]);
});
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Sourcing</h1>
        <p class="text-sm text-gray-500">Request quotes from suppliers and record their responses.</p>
      </div>
      <button class="btn-primary" @click="showForm = !showForm">
        {{ showForm ? "Close" : tab === "rfq" ? "New RFQ" : "New Supplier Quotation" }}
      </button>
    </div>

    <div class="mb-4 flex gap-1 border-b border-gray-200">
      <button class="border-b-2 px-4 py-2 text-sm font-medium"
              :class="tab === 'rfq' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
              @click="tab = 'rfq'; showForm = false">Requests for Quotation</button>
      <button class="border-b-2 px-4 py-2 text-sm font-medium"
              :class="tab === 'sq' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
              @click="tab = 'sq'; showForm = false">Supplier Quotations</button>
    </div>

    <form v-if="showForm && tab === 'rfq'" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
          @submit.prevent="saveRfq">
      <div class="mb-3 grid grid-cols-3 gap-4">
        <div>
          <label class="form-label">Date*</label>
          <input v-model="rfqDate" type="date" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Required By</label>
          <input v-model="rfqScheduleDate" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">Message for Supplier</label>
          <input v-model="rfqMessage" class="form-input" placeholder="Optional note to suppliers" />
        </div>
        <div class="col-span-3">
          <label class="form-label">Suppliers* (hold Ctrl to select several)</label>
          <select v-model="rfqSupplierIds" multiple class="form-input h-24">
            <option v-for="s in accounts.suppliers" :key="s.id" :value="s.id">{{ s.supplier_name }}</option>
          </select>
        </div>
      </div>
      <div v-for="(row, i) in rfqRows" :key="i" class="mb-2 grid grid-cols-12 gap-2">
        <ItemSelect v-model="row.item_id" class="col-span-8" :options="stock.itemOptions" placeholder="Select item…" />
        <input v-model.number="row.qty" type="number" min="0" step="any" placeholder="Qty" class="form-input col-span-4" />
      </div>
      <button type="button" class="btn-secondary" @click="rfqRows.push({ item_id: '', qty: 1 })">Add Row</button>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
      <div class="mt-4 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="saving || !rfqSupplierIds.length">
          {{ saving ? "Saving…" : "Save & Submit" }}
        </button>
      </div>
    </form>

    <form v-if="showForm && tab === 'sq'" class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
          @submit.prevent="saveSq">
      <div class="mb-3 grid grid-cols-3 gap-4">
        <div>
          <label class="form-label">Supplier*</label>
          <select v-model="sqSupplierId" required class="form-input">
            <option value="" disabled>Select…</option>
            <option v-for="s in accounts.suppliers" :key="s.id" :value="s.id">{{ s.supplier_name }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Date*</label>
          <input v-model="sqDate" type="date" required class="form-input" />
        </div>
        <div>
          <label class="form-label">Valid Till</label>
          <input v-model="sqValidTill" type="date" class="form-input" />
        </div>
        <div>
          <label class="form-label">Currency</label>
          <input
            v-model="sqCurrency"
            class="form-input uppercase"
            maxlength="3"
            pattern="[A-Za-z]{3}"
            title="3-letter currency code (e.g. INR), or leave blank for the company default"
            :placeholder="companyCurrency"
          />
        </div>
      </div>
      <div v-for="(row, i) in sqRows" :key="i" class="mb-2 grid grid-cols-12 gap-2">
        <ItemSelect v-model="row.item_id" class="col-span-6" :options="stock.itemOptions" placeholder="Select item…" />
        <input v-model.number="row.qty" type="number" min="0" step="any" placeholder="Qty" class="form-input col-span-3" />
        <input v-model.number="row.rate" type="number" min="0" step="any" placeholder="Quoted rate" class="form-input col-span-3" />
      </div>
      <button type="button" class="btn-secondary" @click="sqRows.push({ item_id: '', qty: 1, rate: 0 })">Add Row</button>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
      <div class="mt-4 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="saving || !sqSupplierId">
          {{ saving ? "Saving…" : "Save & Submit" }}
        </button>
      </div>
    </form>

    <template v-if="tab === 'rfq'">
      <div v-if="selectedRfq" class="mb-4 rounded-lg border border-primary/30 bg-white p-5 shadow-sm">
        <div class="mb-2 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900">{{ selectedRfq.name }}</h2>
          <div class="flex gap-2">
            <PrintButton :path="`/print/Request%20for%20Quotation/${selectedRfq.id}`"
                         :title="`${selectedRfq.name} — Preview`" />
            <SendEmailButton doctype="Request for Quotation" :doc-id="selectedRfq.id" :doc-name="selectedRfq.name" />
            <button class="btn-primary" :disabled="selectedRfq.docstatus !== 1"
                    @click="recordQuoteFor()">Record Supplier Quotation</button>
            <button class="btn-secondary" @click="selectedRfq = null">Close</button>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-6">
          <div>
            <div class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">Items</div>
            <div v-for="item in selectedRfq.items" :key="item.id" class="text-sm text-gray-700">
              {{ item.item_code ?? item.item_name }} — qty {{ Number(item.qty) }}
            </div>
          </div>
          <div>
            <div class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400">Suppliers</div>
            <div v-for="s in selectedRfq.suppliers" :key="s.supplier_id"
                 class="flex items-center justify-between text-sm text-gray-700">
              <span>{{ s.supplier_name ?? "—" }}</span>
              <span class="flex items-center gap-2">
                <StatusBadge :status="s.quote_status" />
                <button v-if="s.quote_status === 'Pending' && selectedRfq.docstatus === 1"
                        class="text-xs text-primary hover:underline"
                        @click="recordQuoteFor(s.supplier_id)">record quote</button>
              </span>
            </div>
          </div>
        </div>
      </div>
      <DataTable :columns="rfqColumns" :rows="rfqList.items.value" :loading="rfqList.loading.value"
                 @row-click="openRfq">
        <template #cell-name="{ row }">
          <span class="font-medium text-gray-900">{{ row.name }}</span>
        </template>
        <template #cell-posting_date="{ value }">
          {{ formatDate(String(value)) }}
        </template>
        <template #cell-status="{ value }">
          <StatusBadge :status="String(value)" />
        </template>
      </DataTable>
      <PaginationFooter :page="rfqList.page.value" :page-size="rfqList.pageSize.value"
                        :total="rfqList.total.value" @go-to="rfqList.goToPage" />
    </template>

    <template v-else>
      <DataTable :columns="sqColumns" :rows="sqList.items.value" :loading="sqList.loading.value">
        <template #cell-name="{ row }">
          <span class="font-medium text-gray-900">{{ row.name }}</span>
        </template>
        <template #cell-supplier_name="{ value }">
          {{ value ?? "—" }}
        </template>
        <template #cell-posting_date="{ value }">
          {{ formatDate(String(value)) }}
        </template>
        <template #cell-grand_total="{ row }">
          {{ formatCurrency(row.grand_total, row.currency ?? companyCurrency) }}
        </template>
        <template #cell-status="{ value, row }">
          <div class="flex items-center gap-2">
            <StatusBadge :status="String(value)" />
            <button v-if="row.docstatus === 1" class="text-xs text-primary hover:underline"
                    @click.stop="makePo(row)">make PO</button>
          </div>
        </template>
      </DataTable>
      <PaginationFooter :page="sqList.page.value" :page-size="sqList.pageSize.value"
                        :total="sqList.total.value" @go-to="sqList.goToPage" />
    </template>
  </div>
</template>
