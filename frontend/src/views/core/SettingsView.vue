<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { brand } from "@/brand";
import { useNamingSeries } from "@/composables/useNamingSeries";
import { useModuleFlagsStore } from "@/stores/moduleFlags";
import type { ErrorEnvelope } from "@/types/core";

const pattern = ref("SINV-.YYYY.-");
const { nextName, loading, error, preview } = useNamingSeries();

const flags = useModuleFlagsStore();
const secretarialOn = ref(false);
const flagsNotice = ref<string | null>(null);
const flagsError = ref<ErrorEnvelope | null>(null);
const flagsSaving = ref(false);

onMounted(async () => {
  await flags.load();
  secretarialOn.value = flags.flags.secretarial === true;
});

async function saveSecretarialFlag(): Promise<void> {
  flagsSaving.value = true;
  flagsNotice.value = null;
  flagsError.value = null;
  try {
    await flags.setSecretarial(secretarialOn.value);
    flagsNotice.value = secretarialOn.value
      ? "Secretarial is on. It now appears on Home and in the sidebar."
      : "Secretarial is off. It is hidden from Home and the sidebar.";
  } catch (e) {
    flagsError.value = e as ErrorEnvelope;
  } finally {
    flagsSaving.value = false;
  }
}
</script>

<template>
  <div class="max-w-2xl space-y-6">
    <h1 class="text-xl font-semibold text-gray-900">Settings</h1>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="text-sm font-semibold text-gray-900">Modules</h2>
      <p class="mt-1 text-sm text-gray-500">
        Secretarial &amp; Compliance is off until you turn it on for this company.
      </p>
      <p v-if="flagsNotice" class="mt-3 rounded bg-green-50 px-3 py-2 text-sm text-green-700">
        {{ flagsNotice }}
      </p>
      <p v-if="flagsError" class="mt-3 rounded bg-red-50 px-3 py-2 text-sm text-red-600">
        {{ flagsError.detail }}
      </p>
      <label class="mt-3 flex items-center gap-3 text-sm text-gray-800">
        <input v-model="secretarialOn" type="checkbox" class="rounded border-gray-300" />
        Enable Secretarial &amp; Compliance
      </label>
      <div class="mt-4 flex justify-end">
        <button type="button" class="btn-primary" :disabled="flagsSaving" @click="saveSecretarialFlag">
          {{ flagsSaving ? "Saving…" : "Save" }}
        </button>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="text-sm font-semibold text-gray-900">Naming Series</h2>
      <p class="mt-1 text-sm text-gray-500">
        Preview document numbering. Tokens: <code>.YYYY.</code> <code>.YY.</code> <code>.MM.</code>
        <code>.DD.</code> and <code>#</code> for counter width.
      </p>
      <div class="mt-3 flex gap-3">
        <input v-model="pattern" type="text" class="form-input flex-1" />
        <button class="btn-primary" :disabled="loading" @click="preview(pattern)">Preview</button>
      </div>
      <p v-if="nextName" class="mt-2 text-sm text-gray-700">
        Next name: <span class="font-mono font-semibold">{{ nextName }}</span>
      </p>
      <p v-if="error" class="mt-2 text-sm text-red-600">{{ error.detail }}</p>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <h2 class="text-sm font-semibold text-gray-900">Branding</h2>
      <p class="mt-1 text-sm text-gray-500">
        All branding is configured in <code>public/brand/config.json</code> — no rebuild required.
      </p>
      <dl class="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div><dt class="text-gray-500">Product</dt><dd class="font-medium">{{ brand.product_name }}</dd></div>
        <div><dt class="text-gray-500">Support</dt><dd class="font-medium">{{ brand.support_email }}</dd></div>
        <div class="flex items-center gap-2">
          <dt class="text-gray-500">Primary</dt>
          <dd><span class="inline-block h-5 w-10 rounded" :style="{ background: brand.primary_color }" /></dd>
        </div>
        <div class="flex items-center gap-2">
          <dt class="text-gray-500">Secondary</dt>
          <dd><span class="inline-block h-5 w-10 rounded" :style="{ background: brand.secondary_color }" /></dd>
        </div>
      </dl>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-sm font-semibold text-gray-900">Document Branding &amp; Print</h2>
          <p class="mt-1 text-sm text-gray-500">
            Logo, addresses, bank details, signatory, theme and what shows on PDFs.
          </p>
        </div>
        <RouterLink to="/settings/print" class="btn-secondary">Configure</RouterLink>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-sm font-semibold text-gray-900">Taxation (GST & Income Tax)</h2>
          <p class="mt-1 text-sm text-gray-500">
            GST settings, returns, TDS returns, and income-tax computation live in the Taxation module.
          </p>
        </div>
        <RouterLink to="/taxation" class="btn-secondary">Open Taxation</RouterLink>
      </div>
    </section>
  </div>
</template>
