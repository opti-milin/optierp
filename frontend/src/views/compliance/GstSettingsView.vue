<script setup lang="ts">
// GST Settings — per-company GST configuration that the India-compliance layer reads
// (registration type, filing cadence, e-invoice / e-way-bill applicability, SEZ). The
// GSTIN + place-of-supply state are derived from the company (set the GSTIN on the Company).
import { onMounted, reactive, ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";

interface GstSettings {
  registration_type: string;
  filing_cadence: string;
  e_invoice_applicable: boolean;
  e_way_bill_applicable: boolean;
  is_sez: boolean;
  gsp_provider: string | null;
  gstin: string | null;
  gst_state: string | null;
}

const form = reactive<GstSettings>({
  registration_type: "Regular",
  filing_cadence: "Monthly",
  e_invoice_applicable: false,
  e_way_bill_applicable: false,
  is_sez: false,
  gsp_provider: null,
  gstin: null,
  gst_state: null,
});
const loading = ref(false);
const saving = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    Object.assign(form, (await api.get<GstSettings>("/gst-settings")).data);
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
    Object.assign(form, (await api.put<GstSettings>("/gst-settings", form)).data);
    notice.value = "GST settings saved.";
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
      <h1 class="text-xl font-semibold text-gray-900">GST Settings</h1>
      <p class="text-sm text-gray-500">
        How this company is registered under GST. The rest of the compliance layer (invoices, returns,
        e-invoice, e-way bill) reads these.
      </p>
    </div>

    <p v-if="notice" class="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Registration</h2>
      <dl class="mb-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-gray-500">GSTIN</dt>
          <dd class="font-mono font-medium">{{ form.gstin || "— not set —" }}</dd>
        </div>
        <div>
          <dt class="text-gray-500">Registered state (GST)</dt>
          <dd class="font-medium">{{ form.gst_state || "—" }}</dd>
        </div>
      </dl>
      <p v-if="!form.gstin" class="mb-4 text-xs text-amber-600">
        No GSTIN on the company yet — set it on the
        <RouterLink to="/companies" class="text-blue-600 hover:underline">Company</RouterLink>
        (its first 2 digits decide your state and intra/inter-state GST).
      </p>

      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">Registration type</label>
          <select v-model="form.registration_type" class="form-input">
            <option value="Regular">Regular</option>
            <option value="Composition">Composition</option>
          </select>
        </div>
        <div>
          <label class="form-label">Filing cadence</label>
          <select v-model="form.filing_cadence" class="form-input">
            <option value="Monthly">Monthly (GSTR-1 + 3B)</option>
            <option value="QRMP">QRMP (quarterly)</option>
          </select>
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Compliance options</h2>
      <div class="space-y-3 text-sm">
        <label class="flex items-start gap-3">
          <input v-model="form.e_invoice_applicable" type="checkbox" class="mt-0.5 rounded border-gray-300" />
          <span>
            <span class="font-medium text-gray-800">E-Invoice applicable</span>
            <span class="block text-xs text-gray-500">Generate an e-invoice (IRN + QR) for B2B sales. Required above the turnover threshold.</span>
          </span>
        </label>
        <label class="flex items-start gap-3">
          <input v-model="form.e_way_bill_applicable" type="checkbox" class="mt-0.5 rounded border-gray-300" />
          <span>
            <span class="font-medium text-gray-800">E-Way Bill applicable</span>
            <span class="block text-xs text-gray-500">Generate e-way bills when transporting goods over ₹50,000.</span>
          </span>
        </label>
        <label class="flex items-start gap-3">
          <input v-model="form.is_sez" type="checkbox" class="mt-0.5 rounded border-gray-300" />
          <span>
            <span class="font-medium text-gray-800">SEZ unit</span>
            <span class="block text-xs text-gray-500">This company operates in a Special Economic Zone (affects place-of-supply / tax treatment).</span>
          </span>
        </label>
      </div>
      <div class="mt-4 max-w-sm">
        <label class="form-label">GSP / IRP provider (for live push)</label>
        <input
          v-model="form.gsp_provider"
          class="form-input"
          placeholder="none (JSON export only)"
        />
        <p class="mt-1 text-xs text-gray-500">
          Name of the GST Suvidha Provider used to push e-invoices / e-way bills / returns. Leave
          blank for JSON-only (download &amp; upload on the portal). Credentials are held securely,
          not here.
        </p>
      </div>
    </section>

    <div class="flex justify-end">
      <button class="btn-primary" :disabled="saving || loading" @click="save">
        {{ saving ? "Saving…" : "Save GST settings" }}
      </button>
    </div>
  </div>
</template>
