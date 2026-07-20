<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import FormBuilder, { type FieldConfig } from "@/components/shared/FormBuilder.vue";
import { useDocument } from "@/composables/useDocument";
import { useCoreStore } from "@/stores/core";
import type { AccountNode, Company } from "@/types/core";
import { api } from "@/api/client";

const props = defineProps<{ id?: string }>();
const router = useRouter();
const core = useCoreStore();
const { doc, error, saving, load, create } = useDocument<Company>("/companies");

const form = ref<Record<string, unknown>>({ chart_of_accounts_template: "standard" });
const accounts = ref<AccountNode[]>([]);
const isNew = computed(() => !props.id);

const fields = computed<FieldConfig[]>(() => [
  { name: "company_name", label: "Company Name", type: "text", required: true, span: 2 },
  { name: "abbr", label: "Abbreviation", type: "text", required: true, help: "Short code, e.g. ACME" },
  {
    name: "default_currency",
    label: "Default Currency",
    type: "select",
    required: true,
    options: core.currencies.map((c) => ({ value: c.code, label: `${c.code} — ${c.currency_name}` })),
  },
  {
    name: "country_code",
    label: "Country",
    type: "select",
    options: [
      { value: "US", label: "United States" },
      { value: "GB", label: "United Kingdom" },
      { value: "IN", label: "India" },
      { value: "AE", label: "United Arab Emirates" },
    ],
    help: "Drives the Chart of Accounts template and fiscal year",
  },
  {
    name: "chart_of_accounts_template",
    label: "Chart of Accounts Template",
    type: "select",
    options: core.coaTemplates.map((t) => ({ value: t, label: t })),
  },
  { name: "tax_id", label: "GSTIN (Tax ID)", type: "text", help: "15-character GSTIN" },
  { name: "pan", label: "PAN", type: "text", help: "10-character Permanent Account Number" },
  { name: "tan", label: "TAN", type: "text", help: "Tax Deduction Account Number (for TDS returns)" },
  { name: "date_of_establishment", label: "Date of Establishment", type: "date" },
]);

async function submit(): Promise<void> {
  const created = await create(form.value);
  if (created) {
    void router.push({ name: "companies" });
  }
}

onMounted(async () => {
  await Promise.all([core.fetchCurrencies(), core.fetchCoaTemplates()]);
  if (props.id) {
    await load(props.id);
    accounts.value = (await api.get<AccountNode[]>(`/companies/${props.id}/chart-of-accounts`)).data;
  }
});
</script>

<template>
  <div class="max-w-3xl">
    <h1 class="mb-4 text-xl font-semibold text-gray-900">
      {{ isNew ? "New Company" : (doc?.company_name ?? "Company") }}
    </h1>

    <form v-if="isNew" class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm" @submit.prevent="submit">
      <FormBuilder v-model="form" :fields="fields" :error-field="error?.field" />
      <p v-if="error" class="mt-3 text-sm text-red-600">{{ error.detail }}</p>
      <div class="mt-6 flex justify-end gap-3">
        <button type="button" class="btn-secondary" @click="router.back()">Cancel</button>
        <button type="submit" class="btn-primary" :disabled="saving">
          {{ saving ? "Creating…" : "Create Company" }}
        </button>
      </div>
    </form>

    <div v-else-if="doc" class="space-y-6">
      <div class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <dl class="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div><dt class="text-gray-500">Abbreviation</dt><dd class="font-medium">{{ doc.abbr }}</dd></div>
          <div><dt class="text-gray-500">Currency</dt><dd class="font-medium">{{ doc.default_currency }}</dd></div>
          <div><dt class="text-gray-500">Country</dt><dd class="font-medium">{{ doc.country_code ?? "—" }}</dd></div>
          <div>
            <dt class="text-gray-500">COA Template</dt>
            <dd class="font-medium">{{ doc.chart_of_accounts_template ?? "—" }}</dd>
          </div>
          <div><dt class="text-gray-500">GSTIN</dt><dd class="font-mono font-medium">{{ doc.tax_id ?? "—" }}</dd></div>
          <div><dt class="text-gray-500">PAN</dt><dd class="font-mono font-medium">{{ doc.pan ?? "—" }}</dd></div>
          <div><dt class="text-gray-500">TAN</dt><dd class="font-mono font-medium">{{ doc.tan ?? "—" }}</dd></div>
        </dl>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h2 class="mb-3 text-sm font-semibold text-gray-900">Chart of Accounts ({{ accounts.length }} accounts)</h2>
        <ul class="max-h-96 space-y-0.5 overflow-y-auto text-sm">
          <li
            v-for="account in accounts"
            :key="account.id"
            :style="{ paddingLeft: `${(account.path.split('.').length - 1) * 16}px` }"
            class="text-gray-700"
          >
            <span :class="account.is_group ? 'font-semibold' : ''">{{ account.account_name }}</span>
            <span v-if="account.account_type" class="ml-2 text-xs text-gray-400">{{ account.account_type }}</span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
