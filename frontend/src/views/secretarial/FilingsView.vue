<script setup lang="ts">
// ROC filings, and — the part that matters — what authorised each one.
//
// Anyone can keep a list of SRNs. The question an inspection actually asks is "on whose
// authority was this filed", so every row expands into the chain resolution → document →
// form → SRN → challan. Where a link is missing the chain says so rather than presenting
// an incomplete trail as a sound one.
import { ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { Filing, Meeting } from "@/types/secretarial";

const store = useSecretarialStore();

const filings = ref<Filing[]>([]);
const meetings = ref<Meeting[]>([]);
const chains = ref<Record<string, any>>({});
const openChain = ref<string | null>(null);

const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const busy = ref("");
const creating = ref(false);
const statusFilter = ref("");

const form = ref({ form_code: "", fy: "", srn: "", filed_on: "", status: "prepared", meeting_id: "" });

const STATUS_TONE: Record<string, string> = {
  prepared: "bg-gray-100 text-gray-600",
  filed: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  resubmission: "bg-amber-100 text-amber-800",
  rejected: "bg-red-100 text-red-800",
};

function fail(e: unknown): void {
  error.value = e as ErrorEnvelope;
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    filings.value = (
      await api.get<ListResponse<Filing>>("/secretarial/filings", {
        params: { entity_id: store.entityId, status: statusFilter.value || undefined, page_size: 100 },
      })
    ).data.items;
    meetings.value = (
      await api.get<ListResponse<Meeting>>("/secretarial/meetings", {
        params: { entity_id: store.entityId, page_size: 100 },
      })
    ).data.items;
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

async function create(): Promise<void> {
  if (!store.entityId) return;
  busy.value = "create";
  error.value = null;
  try {
    await api.post("/secretarial/filings", {
      entity_id: store.entityId,
      form_code: form.value.form_code,
      fy: form.value.fy || null,
      srn: form.value.srn || null,
      filed_on: form.value.filed_on || null,
      status: form.value.status,
      meeting_id: form.value.meeting_id || null,
    });
    creating.value = false;
    form.value = { form_code: "", fy: "", srn: "", filed_on: "", status: "prepared", meeting_id: "" };
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function toggleChain(filing: Filing): Promise<void> {
  if (openChain.value === filing.id) {
    openChain.value = null;
    return;
  }
  try {
    chains.value[filing.id] = (await api.get(`/secretarial/filings/${filing.id}/chain`)).data;
    openChain.value = filing.id;
  } catch (e) {
    fail(e);
  }
}

watch(() => [store.entityId, statusFilter.value], load);
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Filings</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          Forms filed with the Registrar, each traceable back to the resolution that authorised it.
        </p>
      </div>
      <button
        class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        @click="creating = !creating"
      >
        {{ creating ? "Cancel" : "Record a filing" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <form
      v-if="creating"
      class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-3 lg:grid-cols-6"
      @submit.prevent="create"
    >
      <label class="text-xs text-gray-500">
        Form
        <input
          v-model="form.form_code"
          required
          placeholder="MGT-7"
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
      </label>
      <label class="text-xs text-gray-500">
        FY
        <input
          v-model="form.fy"
          placeholder="2025-26"
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
      </label>
      <label class="text-xs text-gray-500">
        SRN
        <input v-model="form.srn" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Filed on
        <input v-model="form.filed_on" type="date" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Authorised by
        <select v-model="form.meeting_id" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option value="">—</option>
          <option v-for="m in meetings" :key="m.id" :value="m.id">
            {{ m.title || m.meeting_type }} · {{ new Date(m.scheduled_at).toLocaleDateString() }}
          </option>
        </select>
      </label>
      <div class="flex items-end">
        <button
          class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
          :disabled="busy !== ''"
        >
          Save
        </button>
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
            <th class="px-3 py-2">Form</th>
            <th class="px-3 py-2">FY</th>
            <th class="px-3 py-2">SRN</th>
            <th class="px-3 py-2">Filed</th>
            <th class="px-3 py-2">State</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <template v-for="f in filings" :key="f.id">
            <tr class="hover:bg-gray-50">
              <td class="px-3 py-2 font-medium text-gray-900">{{ f.form_code }}</td>
              <td class="px-3 py-2 text-xs text-gray-500">{{ f.fy || "—" }}</td>
              <td class="px-3 py-2 font-mono text-xs text-gray-600">{{ f.srn || "—" }}</td>
              <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">{{ f.filed_on || "—" }}</td>
              <td class="px-3 py-2">
                <span class="rounded px-1.5 py-0.5 text-xs" :class="STATUS_TONE[f.status]">{{ f.status }}</span>
              </td>
              <td class="px-3 py-2 text-right">
                <button class="text-xs text-blue-600 hover:underline" @click="toggleChain(f)">
                  {{ openChain === f.id ? "Hide" : "On what authority?" }}
                </button>
              </td>
            </tr>

            <tr v-if="openChain === f.id" class="bg-gray-50">
              <td colspan="6" class="px-3 py-3">
                <div class="flex flex-wrap items-center gap-2 text-xs">
                  <span
                    class="rounded border px-2 py-1"
                    :class="chains[f.id]?.resolution ? 'border-green-300 bg-green-50 text-green-800' : 'border-red-200 bg-red-50 text-red-700'"
                  >
                    <RouterLink
                      v-if="chains[f.id]?.resolution?.meeting_id"
                      :to="'/secretarial/meetings/' + chains[f.id].resolution.meeting_id"
                      class="underline"
                    >
                      {{ chains[f.id]?.resolution?.title || "Resolution" }}
                    </RouterLink>
                    <span v-else>{{ chains[f.id]?.resolution?.title || "No resolution" }}</span>
                  </span>
                  <span class="text-gray-400">→</span>
                  <span
                    class="rounded border px-2 py-1"
                    :class="chains[f.id]?.document ? 'border-green-300 bg-green-50 text-green-800' : 'border-red-200 bg-red-50 text-red-700'"
                  >
                    {{ chains[f.id]?.document?.title || "No document" }}
                  </span>
                  <span class="text-gray-400">→</span>
                  <span class="rounded border border-gray-300 bg-white px-2 py-1 text-gray-700">
                    {{ chains[f.id]?.filing?.form_code }}
                  </span>
                  <span class="text-gray-400">→</span>
                  <span
                    class="rounded border px-2 py-1"
                    :class="chains[f.id]?.filing?.srn ? 'border-green-300 bg-green-50 text-green-800' : 'border-red-200 bg-red-50 text-red-700'"
                  >
                    {{ chains[f.id]?.filing?.srn || "No SRN" }}
                  </span>
                  <span class="text-gray-400">→</span>
                  <span
                    class="rounded border px-2 py-1"
                    :class="chains[f.id]?.filing?.has_challan ? 'border-green-300 bg-green-50 text-green-800' : 'border-red-200 bg-red-50 text-red-700'"
                  >
                    {{ chains[f.id]?.filing?.has_challan ? "Challan attached" : "No challan" }}
                  </span>
                </div>

                <p
                  v-if="chains[f.id]?.complete"
                  class="mt-2 text-xs text-green-700"
                >
                  The chain is complete.
                </p>
                <ul v-else class="mt-2 space-y-0.5">
                  <li v-for="(g, i) in chains[f.id]?.gaps ?? []" :key="i" class="text-xs text-red-700">• {{ g }}</li>
                </ul>
              </td>
            </tr>
          </template>
          <tr v-if="!filings.length">
            <td colspan="6" class="px-3 py-8 text-center text-sm text-gray-400">No filings recorded yet.</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
