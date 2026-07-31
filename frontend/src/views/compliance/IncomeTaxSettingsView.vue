<script setup lang="ts">
// Income Tax Settings — per-company entity ITR policy (Phase 0).
// PAN / TAN are derived from the Company (set them on the Company form).
import { onMounted, reactive, ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { IncomeTaxSettings } from "@/types/compliance";

const form = reactive<IncomeTaxSettings>({
  entity_type: "Company",
  filing_regime: "Normal",
  default_assessment_year: null,
  itr_efile_provider: null,
  pan: null,
  tan: null,
});
const loading = ref(false);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    Object.assign(form, (await api.get<IncomeTaxSettings>("/income-tax-settings")).data);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    Object.assign(form, (await api.put<IncomeTaxSettings>("/income-tax-settings", form)).data);
    notice.value = "Income tax settings saved.";
    setTimeout(() => (notice.value = null), 2500);
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="max-w-2xl space-y-6">
    <div>
      <h1 class="text-xl font-semibold text-gray-900">Income Tax Settings</h1>
      <p class="text-sm text-gray-500">
        How this company files its income-tax return (entity or individual). PAN and TAN come from
        the Company record.
      </p>
    </div>

    <p v-if="notice" class="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Identity</h2>
      <dl class="mb-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-gray-500">PAN</dt>
          <dd class="font-mono font-medium">{{ form.pan || "— not set —" }}</dd>
        </div>
        <div>
          <dt class="text-gray-500">TAN</dt>
          <dd class="font-mono font-medium">{{ form.tan || "— not set —" }}</dd>
        </div>
      </dl>
      <p v-if="!form.pan || !form.tan" class="mb-4 text-xs text-amber-600">
        Set PAN / TAN on the
        <RouterLink to="/companies" class="text-blue-600 hover:underline">Company</RouterLink>
        (needed for ITR headers and TDS Form 26Q).
      </p>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">Entity type</label>
          <select v-model="form.entity_type" class="form-input">
            <option value="Company">Company (ITR-6)</option>
            <option value="Proprietor">Proprietor (ITR-3, slabs)</option>
            <option value="Individual">Individual (ITR-1 / Form 16, slabs)</option>
            <option value="Firm">Firm (ITR-5)</option>
            <option value="LLP">LLP (ITR-5)</option>
          </select>
        </div>
        <div>
          <label class="form-label">Filing regime</label>
          <select v-model="form.filing_regime" class="form-input">
            <option value="Normal">Normal</option>
            <option value="New">New</option>
          </select>
        </div>
        <p class="col-span-2 text-xs text-gray-500">
          After changing entity type or filing regime, open a draft computation and click
          <em>Apply rate for current entity / regime</em> so tax uses the matching rate table.
        </p>
        <div class="col-span-2">
          <label class="form-label">Default assessment year</label>
          <input
            v-model="form.default_assessment_year"
            type="text"
            class="form-input"
            placeholder="e.g. 2025-26"
          />
        </div>
        <div class="col-span-2">
          <label class="form-label">E-file provider</label>
          <input
            v-model="form.itr_efile_provider"
            type="text"
            class="form-input"
            placeholder="none (JSON export only)"
          />
          <p class="mt-1 text-xs text-gray-500">
            Provider name for live/sandbox push (<code>none</code> or empty = JSON only;
            <code>sandbox</code> = stub acknowledgement). Credentials stay out-of-band — never in
            this form.
          </p>
        </div>
      </div>
    </section>

    <div class="flex justify-end gap-3">
      <button type="button" class="btn-secondary" :disabled="loading" @click="load">Reload</button>
      <button type="button" class="btn-primary" :disabled="saving || loading" @click="save">
        {{ saving ? "Saving…" : "Save" }}
      </button>
    </div>
  </div>
</template>
