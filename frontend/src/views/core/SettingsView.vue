<script setup lang="ts">
import { ref } from "vue";
import { RouterLink } from "vue-router";
import { brand } from "@/brand";
import { useNamingSeries } from "@/composables/useNamingSeries";

const pattern = ref("SINV-.YYYY.-");
const { nextName, loading, error, preview } = useNamingSeries();
</script>

<template>
  <div class="max-w-2xl space-y-6">
    <h1 class="text-xl font-semibold text-gray-900">Settings</h1>

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
          <h2 class="text-sm font-semibold text-gray-900">GST / India Compliance</h2>
          <p class="mt-1 text-sm text-gray-500">
            Registration type, filing cadence, and e-invoice / e-way-bill applicability.
          </p>
        </div>
        <RouterLink to="/gst-settings" class="btn-secondary">Configure</RouterLink>
      </div>
    </section>
  </div>
</template>
