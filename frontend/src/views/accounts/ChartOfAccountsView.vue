<script setup lang="ts">
// Chart of Accounts: browse the account tree, add a child under any group, and
// edit an account (rename, type, currency, freeze/disable). Backed by the
// bespoke API: GET /companies/{id}/chart-of-accounts, POST /accounts,
// PATCH /accounts/{id}. A rename cascades the ltree path server-side.

import { computed, nextTick, onMounted, ref } from "vue";
import { api } from "@/api/client";
import { useAccountsStore } from "@/stores/accounts";
import type { AccountNode, ErrorEnvelope } from "@/types/core";

const store = useAccountsStore();
// The add/edit form renders above the (scrollable) tree; when the clicked row is
// scrolled down, the form would otherwise open off-screen above the viewport — so
// bring it into view on open.
const formEl = ref<HTMLElement | null>(null);
function revealForm(): void {
  nextTick(() => formEl.value?.scrollIntoView({ behavior: "smooth", block: "start" }));
}

const error = ref<ErrorEnvelope | null>(null);
const loading = ref(false);
const expanded = ref<Set<string>>(new Set());

const ACCOUNT_TYPES = [
  "", "Bank", "Cash", "Receivable", "Payable", "Tax", "Income Account",
  "Cost of Goods Sold", "Expense Account", "Fixed Asset", "Stock",
  "Stock Received But Not Billed", "Stock Adjustment", "Round Off",
  "Equity", "Depreciation", "Accumulated Depreciation", "Temporary",
];

const CM_CLASSES = [
  { value: "", label: "—" },
  { value: "revenue", label: "Revenue" },
  { value: "variable_cost", label: "Variable cost" },
  { value: "product_channel_fixed", label: "Product / Channel fixed" },
  { value: "segment_bu_fixed", label: "Segment / BU fixed" },
  { value: "corporate_overhead", label: "Corporate overhead" },
];

const CM_CLASS_SHORT: Record<string, string> = {
  revenue: "Rev",
  variable_cost: "Var",
  product_channel_fixed: "Prod",
  segment_bu_fixed: "Seg",
  corporate_overhead: "Corp",
};

const showForm = ref(false);
const saving = ref(false);
const editId = ref<string | null>(null); // null = creating a child; set = editing
const parentId = ref<string>("");
const form = ref({
  account_name: "",
  account_number: "",
  account_type: "",
  cm_class: "",
  is_group: false,
  account_currency: "",
  freeze_account: false,
  disabled: false,
});

const accountsById = computed(
  () => new Map(store.accounts.map((a: AccountNode) => [a.id, a])),
);
const childrenByParent = computed(() => {
  const map = new Map<string | null, AccountNode[]>();
  for (const a of store.accounts) {
    const key = a.parent_account_id;
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(a);
  }
  for (const list of map.values()) {
    list.sort((x, y) => x.account_name.localeCompare(y.account_name));
  }
  return map;
});

const roots = computed(() =>
  store.accounts
    .filter((a) => !a.parent_account_id || !accountsById.value.has(a.parent_account_id))
    .sort((x, y) => x.account_name.localeCompare(y.account_name)),
);

interface Row {
  node: AccountNode;
  depth: number;
  hasChildren: boolean;
}

const rows = computed<Row[]>(() => {
  const out: Row[] = [];
  const walk = (node: AccountNode, depth: number): void => {
    const kids = childrenByParent.value.get(node.id) ?? [];
    out.push({ node, depth, hasChildren: kids.length > 0 });
    if (expanded.value.has(node.id)) {
      for (const k of kids) walk(k, depth + 1);
    }
  };
  for (const r of roots.value) walk(r, 0);
  return out;
});

function toggle(id: string): void {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}

function resetForm(): void {
  form.value = {
    account_name: "", account_number: "", account_type: "", cm_class: "",
    is_group: false, account_currency: "", freeze_account: false, disabled: false,
  };
}

function openAddChild(parent: AccountNode): void {
  editId.value = null;
  parentId.value = parent.id;
  resetForm();
  showForm.value = true;
  expanded.value = new Set(expanded.value).add(parent.id);
  revealForm();
}

function openEdit(node: AccountNode): void {
  editId.value = node.id;
  parentId.value = node.parent_account_id ?? "";
  form.value = {
    account_name: node.account_name,
    account_number: node.account_number ?? "",
    account_type: node.account_type ?? "",
    cm_class: node.cm_class ?? "",
    is_group: node.is_group,
    account_currency: node.account_currency ?? "",
    freeze_account: !!node.freeze_account,
    disabled: !!node.disabled,
  };
  showForm.value = true;
  revealForm();
}

const parentName = computed(() => accountsById.value.get(parentId.value)?.account_name ?? "");
const editingIsPnlLeaf = computed(() => {
  if (!editId.value) {
    const parent = accountsById.value.get(parentId.value);
    return parent?.report_type === "Profit and Loss" && !form.value.is_group;
  }
  const node = accountsById.value.get(editId.value);
  return node?.report_type === "Profit and Loss" && !node.is_group;
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    await store.fetchAccounts();
    if (expanded.value.size === 0) {
      expanded.value = new Set(roots.value.map((r) => r.id));
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  try {
    if (editId.value) {
      await api.patch(`/accounts/${editId.value}`, {
        account_name: form.value.account_name,
        account_number: form.value.account_number || null,
        account_type: form.value.account_type || null,
        cm_class: editingIsPnlLeaf.value ? (form.value.cm_class || null) : null,
        account_currency: form.value.account_currency || null,
        freeze_account: form.value.freeze_account,
        disabled: form.value.disabled,
      });
    } else {
      await api.post("/accounts", {
        account_name: form.value.account_name,
        parent_account_id: parentId.value,
        account_number: form.value.account_number || null,
        account_type: form.value.account_type || null,
        cm_class: editingIsPnlLeaf.value ? (form.value.cm_class || null) : null,
        is_group: form.value.is_group,
        account_currency: form.value.account_currency || null,
      });
    }
    showForm.value = false;
    await store.fetchAccounts();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="max-w-4xl">
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Chart of Accounts</h1>
        <p class="text-sm text-gray-500">
          The tree of ledger accounts. Add a child under any <span class="font-medium">group</span>, or edit an account.
        </p>
      </div>
      <button class="btn-secondary" :disabled="loading" @click="load">Refresh</button>
    </div>

    <p v-if="error" class="mb-3 text-sm text-red-600">{{ error.detail }}</p>

    <form
      v-if="showForm"
      ref="formEl"
      class="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm scroll-mt-4"
      @submit.prevent="save"
    >
      <p class="mb-3 text-sm text-gray-600">
        <template v-if="editId">Editing <span class="font-semibold text-gray-900">{{ form.account_name }}</span></template>
        <template v-else>New account under <span class="font-semibold text-gray-900">{{ parentName }}</span></template>
      </p>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">Account Name*</label>
          <input v-model="form.account_name" required class="form-input" placeholder="e.g. HDFC Bank" />
        </div>
        <div>
          <label class="form-label">Account Number</label>
          <input v-model="form.account_number" class="form-input" placeholder="optional" />
        </div>
        <div>
          <label class="form-label">Account Type</label>
          <select v-model="form.account_type" class="form-input">
            <option v-for="t in ACCOUNT_TYPES" :key="t" :value="t">{{ t || "—" }}</option>
          </select>
        </div>
        <div v-if="editingIsPnlLeaf">
          <label class="form-label">CM Class</label>
          <select v-model="form.cm_class" class="form-input">
            <option v-for="c in CM_CLASSES" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Currency</label>
          <input v-model="form.account_currency" class="form-input" placeholder="defaults to company" />
        </div>
        <label v-if="!editId" class="col-span-2 flex items-center gap-2 text-sm text-gray-700">
          <input v-model="form.is_group" type="checkbox" />
          Is a group (can hold child accounts)
        </label>
        <template v-else>
          <label class="flex items-center gap-2 text-sm text-gray-700">
            <input v-model="form.freeze_account" type="checkbox" />
            Freeze (block postings)
          </label>
          <label class="flex items-center gap-2 text-sm text-gray-700">
            <input v-model="form.disabled" type="checkbox" />
            Disabled
          </label>
        </template>
      </div>
      <div class="mt-4 flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="showForm = false">Cancel</button>
        <button type="submit" class="btn-primary" :disabled="saving || !form.account_name">
          {{ saving ? "Saving…" : editId ? "Save Changes" : "Add Account" }}
        </button>
      </div>
    </form>

    <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase text-gray-500">
          <tr>
            <th class="px-4 py-2">Account</th>
            <th class="px-4 py-2">Type</th>
            <th class="px-4 py-2">CM</th>
            <th class="px-4 py-2">Root</th>
            <th class="px-4 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.node.id" class="border-t border-gray-100" :class="{ 'opacity-50': row.node.disabled }">
            <td class="px-4 py-2">
              <span :style="{ paddingLeft: row.depth * 18 + 'px' }" class="inline-flex items-center gap-1">
                <button
                  v-if="row.hasChildren"
                  class="w-4 text-gray-400 hover:text-gray-700"
                  @click="toggle(row.node.id)"
                >{{ expanded.has(row.node.id) ? "▾" : "▸" }}</button>
                <span v-else class="inline-block w-4"></span>
                <span v-if="row.node.account_number" class="text-gray-400">{{ row.node.account_number }}</span>
                <span :class="row.node.is_group ? 'font-semibold text-gray-900' : 'text-gray-700'">
                  {{ row.node.account_name }}
                </span>
                <span v-if="row.node.is_group" class="rounded bg-gray-100 px-1.5 text-[10px] uppercase text-gray-500">group</span>
                <span v-if="row.node.freeze_account" class="rounded bg-amber-100 px-1.5 text-[10px] uppercase text-amber-700">frozen</span>
              </span>
            </td>
            <td class="px-4 py-2 text-gray-600">{{ row.node.account_type || "—" }}</td>
            <td class="px-4 py-2 text-gray-500">
              <span v-if="row.node.cm_class" class="rounded bg-slate-100 px-1.5 text-[10px] uppercase text-slate-700">
                {{ CM_CLASS_SHORT[row.node.cm_class] || row.node.cm_class }}
              </span>
              <span v-else>—</span>
            </td>
            <td class="px-4 py-2 text-gray-500">{{ row.node.root_type }}</td>
            <td class="px-4 py-2 text-right whitespace-nowrap">
              <button class="text-xs font-medium text-gray-500 hover:underline" @click="openEdit(row.node)">Edit</button>
              <button
                v-if="row.node.is_group"
                class="ml-3 text-xs font-medium text-primary hover:underline"
                @click="openAddChild(row.node)"
              >+ Add child</button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="5" class="px-4 py-8 text-center text-gray-400">
              {{ loading ? "Loading…" : "No accounts yet." }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
