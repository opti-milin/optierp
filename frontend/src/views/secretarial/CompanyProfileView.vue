<script setup lang="ts">
// Business mode: the company's own statutory profile.
//
// There is exactly one company here, so this is a form, not a list. A firm that
// sells appliances should never be shown a table with one row in it, nor asked
// to pick which "entity" they mean.
import { ref, watch } from "vue";
import { api } from "@/api/client";
import CompanyForm from "@/components/secretarial/CompanyForm.vue";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope } from "@/types/core";
import type { EntityDetail } from "@/types/secretarial";

const store = useSecretarialStore();
const detail = ref<EntityDetail | null>(null);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const saved = ref(false);

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    detail.value = (await api.get<EntityDetail>(`/secretarial/entities/${store.entityId}`)).data;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function onSaved(): Promise<void> {
  saved.value = true;
  await store.refreshEntities();
  await load();
  setTimeout(() => (saved.value = false), 2500);
}

watch(() => store.entityId, load);

(async () => {
  await store.load();
  await load();
})();
</script>

<template>
  <div class="max-w-3xl">
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Company details</h1>
      <p class="text-sm text-gray-500">
        The statutory particulars behind your registers and filings.
      </p>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="saved" class="mb-3 rounded border border-green-200 bg-green-50 p-2 text-sm text-green-800">
      Saved.
    </p>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div v-if="detail" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <CompanyForm :existing="detail" mode="company" @saved="onSaved" @cancel="load" />
    </div>
  </div>
</template>
