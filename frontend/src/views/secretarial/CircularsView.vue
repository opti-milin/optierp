<script setup lang="ts">
// Resolutions passed by circulation (s.175) — the register of them.
//
// Drafting one is the easy half; the hard half is that some matters simply cannot be
// decided this way, so the draft form here does no gatekeeping and the detail page does
// all of it, immediately before the irreversible step.
import { ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { Circular } from "@/types/secretarial";

const store = useSecretarialStore();

const circulars = ref<Circular[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const statusFilter = ref("");
const creating = ref(false);

const form = ref({ title: "", resolution_text: "", description: "", consent_rule: "majority" });

const STATUS_TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  circulating: "bg-blue-100 text-blue-800",
  passed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  expired: "bg-gray-200 text-gray-500",
  ratified: "bg-green-100 text-green-800",
};

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    circulars.value = (
      await api.get<ListResponse<Circular>>("/secretarial/circulars", {
        params: { entity_id: store.entityId, status: statusFilter.value || undefined, page_size: 100 },
      })
    ).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function create(): Promise<void> {
  if (!store.entityId) return;
  error.value = null;
  try {
    await api.post("/secretarial/circulars", {
      entity_id: store.entityId,
      title: form.value.title,
      resolution_text: form.value.resolution_text,
      description: form.value.description || null,
      consent_rule: form.value.consent_rule,
    });
    creating.value = false;
    form.value = { title: "", resolution_text: "", description: "", consent_rule: "majority" };
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

watch(() => [store.entityId, statusFilter.value], load);
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Circular resolutions</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          Decisions taken between board meetings under s.175. Directors consent from a link —
          no login — and the outcome is computed from their responses, never typed in.
        </p>
      </div>
      <button
        class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        @click="creating = !creating"
      >
        {{ creating ? "Cancel" : "Draft a resolution" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <form
      v-if="creating"
      class="mb-4 space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
      @submit.prevent="create"
    >
      <label class="block text-xs text-gray-500">
        Title
        <input
          v-model="form.title"
          required
          placeholder="e.g. Opening of a current account with HDFC Bank"
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
      </label>
      <label class="block text-xs text-gray-500">
        Resolution text
        <textarea
          v-model="form.resolution_text"
          required
          rows="4"
          placeholder="RESOLVED THAT …"
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        ></textarea>
      </label>
      <div class="flex flex-wrap items-end gap-3">
        <label class="text-xs text-gray-500">
          Passes on
          <select v-model="form.consent_rule" class="mt-1 block rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option value="majority">Simple majority</option>
            <option value="two_thirds">Two-thirds</option>
            <option value="unanimous">Unanimous</option>
          </select>
        </label>
        <button class="rounded bg-gray-900 px-4 py-2 text-sm text-white hover:bg-gray-800">Save draft</button>
      </div>
    </form>

    <div class="mb-3">
      <select v-model="statusFilter" class="rounded border border-gray-300 px-2 py-1 text-sm">
        <option value="">All states</option>
        <option v-for="s in Object.keys(STATUS_TONE)" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>

    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div v-else class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Reference</th>
            <th class="px-3 py-2">Resolution</th>
            <th class="px-3 py-2">Passes on</th>
            <th class="px-3 py-2">Circulated</th>
            <th class="px-3 py-2">State</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="c in circulars" :key="c.id" class="hover:bg-gray-50">
            <td class="whitespace-nowrap px-3 py-2 font-mono text-xs text-gray-500">
              {{ c.reference_no || "—" }}
            </td>
            <td class="px-3 py-2">
              <RouterLink
                :to="'/secretarial/circulars/' + c.id"
                class="font-medium text-blue-600 hover:underline"
              >
                {{ c.title }}
              </RouterLink>
            </td>
            <td class="px-3 py-2 text-xs text-gray-500">{{ c.consent_rule.replace("_", " ") }}</td>
            <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">
              {{ c.circulated_at ? new Date(c.circulated_at).toLocaleDateString() : "—" }}
            </td>
            <td class="px-3 py-2">
              <span class="rounded px-1.5 py-0.5 text-xs" :class="STATUS_TONE[c.status]">{{ c.status }}</span>
            </td>
          </tr>
          <tr v-if="!circulars.length">
            <td colspan="5" class="px-3 py-8 text-center text-sm text-gray-400">
              No circular resolutions yet.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
