<script setup lang="ts">
// Taxation Settings — enable/disable the Taxation module for this company.
import { onMounted, ref } from "vue";
import type { ErrorEnvelope } from "@/types/core";
import { useModuleFlagsStore } from "@/stores/moduleFlags";

const flags = useModuleFlagsStore();
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);
const saving = ref(false);
const loaded = ref(false);
const fModuleEnabled = ref(true);

async function load(): Promise<void> {
  error.value = null;
  try {
    await flags.load();
    fModuleEnabled.value = flags.flags.taxation !== false;
    loaded.value = true;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    await flags.setTaxation(fModuleEnabled.value);
    notice.value = "Settings saved.";
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <h1 class="text-xl font-semibold text-gray-900">Taxation Settings</h1>
    <p class="mb-4 text-sm text-gray-500">
      Show or hide the Taxation module (GST returns, TDS returns, Income Tax) in navigation.
    </p>

    <p v-if="notice" class="mb-3 rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <section v-if="loaded" class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
      <label class="flex items-center gap-3 text-sm text-gray-800">
        <input v-model="fModuleEnabled" type="checkbox" class="rounded border-gray-300" />
        Enable Taxation module
      </label>
      <p class="mt-2 text-xs text-gray-500">
        When off, Taxation is hidden from the home launcher and global navigation. Invoice tax
        templates remain under Accounting.
      </p>
      <div class="mt-6 flex justify-end">
        <button type="button" class="btn-primary" :disabled="saving" @click="save">
          {{ saving ? "Saving…" : "Save" }}
        </button>
      </div>
    </section>
  </div>
</template>
