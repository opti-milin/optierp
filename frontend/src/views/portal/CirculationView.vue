<script setup lang="ts">
// /p/c/:token — "here are the papers for the next board meeting".
//
// Opening this page is itself the evidence: the GET marks the recipient viewed, and
// acknowledging marks them acknowledged. Both are append-only on the server, which is
// why there is nothing here that lets a director undo either.
import { ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { PortalCirculation } from "@/types/secretarial";
import PortalShell from "./PortalShell.vue";

const route = useRoute();
const token = String(route.params.token ?? "");

const data = ref<PortalCirculation | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const acknowledging = ref(false);

function docHref(documentId: string): string {
  // A plain link works because the portal is unauthenticated by design — no bearer
  // token to attach, so the browser can fetch it directly.
  return `/api/v1/portal/c/${encodeURIComponent(token)}/documents/${documentId}`;
}

async function load(): Promise<void> {
  loading.value = true;
  try {
    data.value = (await api.get<PortalCirculation>(`/portal/c/${encodeURIComponent(token)}`)).data;
  } catch (e) {
    error.value = (e as ErrorEnvelope).detail ?? "This link is not valid.";
  } finally {
    loading.value = false;
  }
}

async function acknowledge(): Promise<void> {
  acknowledging.value = true;
  try {
    data.value = (
      await api.post<PortalCirculation>(`/portal/c/${encodeURIComponent(token)}/acknowledge`)
    ).data;
    // The acknowledge response carries no documents, so keep the ones already shown.
    await load();
  } catch (e) {
    error.value = (e as ErrorEnvelope).detail ?? "Could not record your acknowledgement.";
  } finally {
    acknowledging.value = false;
  }
}

load();
</script>

<template>
  <PortalShell
    :entity-name="data?.entity_name"
    heading="Board papers"
    :loading="loading"
    :error="error"
  >
    <div v-if="data" class="space-y-4">
      <div class="rounded-lg bg-white p-5 shadow-sm">
        <h2 class="text-base font-semibold text-gray-900">{{ data.subject }}</h2>
        <p class="mt-1 text-sm text-gray-500">For {{ data.recipient_name }}</p>
        <p v-if="data.message" class="mt-3 whitespace-pre-line text-sm text-gray-700">
          {{ data.message }}
        </p>
      </div>

      <div v-if="data.documents.length" class="rounded-lg bg-white p-5 shadow-sm">
        <h3 class="mb-3 text-xs uppercase tracking-wide text-gray-400">
          {{ data.documents.length }} document{{ data.documents.length === 1 ? "" : "s" }}
        </h3>
        <ul class="divide-y divide-gray-100">
          <li v-for="doc in data.documents" :key="doc.id" class="flex items-center justify-between py-3">
            <div class="min-w-0 pr-3">
              <p class="truncate text-sm font-medium text-gray-900">{{ doc.title }}</p>
              <p class="text-xs text-gray-400">{{ doc.document_type }}</p>
            </div>
            <a
              :href="docHref(doc.id)"
              class="shrink-0 rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              Download
            </a>
          </li>
        </ul>
      </div>

      <div class="rounded-lg bg-white p-5 shadow-sm">
        <div v-if="data.acknowledged_at">
          <p class="text-sm font-medium text-green-700">Receipt acknowledged</p>
          <p class="mt-1 text-xs text-gray-500">
            Recorded {{ new Date(data.acknowledged_at).toLocaleString() }}
          </p>
        </div>
        <div v-else>
          <p class="mb-3 text-sm text-gray-600">
            Confirming receipt tells the company you have the papers. It is not approval of
            anything in them.
          </p>
          <button
            class="w-full rounded bg-gray-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50 sm:w-auto"
            :disabled="acknowledging"
            @click="acknowledge"
          >
            {{ acknowledging ? "Recording…" : "Acknowledge receipt" }}
          </button>
        </div>
      </div>
    </div>
  </PortalShell>
</template>
