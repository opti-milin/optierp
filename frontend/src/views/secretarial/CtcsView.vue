<script setup lang="ts">
// Certified true copies — the register of what was certified, to whom, and when.
//
// A CTC ends up in a bank's file, so it is append-only: there is no edit and no delete.
// A mistake is corrected by issuing a fresh copy that names the one it supersedes and
// says why. The resolution text is prefilled from the source record rather than retyped,
// because a certified copy that does not match the minute book is the whole failure mode.
import { computed, ref, watch } from "vue";
import { api, openPdf } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { Circular, Ctc, Meeting } from "@/types/secretarial";

const store = useSecretarialStore();

const ctcs = ref<Ctc[]>([]);
const meetings = ref<Meeting[]>([]);
const circulars = ref<Circular[]>([]);

const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const busy = ref("");
const composing = ref(false);

const passageMode = ref("board");
const sourceId = ref("");
const resolutionText = ref("");
const passedOn = ref("");
const issuedTo = ref("");
const purpose = ref("");
const place = ref("");
const available = ref<{ person_id: string; name: string; din: string; designation: string }[]>([]);
const chosen = ref<string[]>([]);

const MODES = [
  { key: "board", label: "Board meeting" },
  { key: "circular", label: "Circular resolution" },
  { key: "agm", label: "AGM" },
  { key: "egm", label: "EGM" },
  { key: "committee", label: "Committee meeting" },
  { key: "other", label: "Other" },
];

const usesCircular = computed(() => passageMode.value === "circular");

// Only a passed resolution can be certified — offering the others would just produce a
// server error after the user has filled the form in.
const sourceOptions = computed(() => {
  if (usesCircular.value) {
    return circulars.value
      .filter((c) => ["passed", "ratified"].includes(c.status))
      .map((c) => ({ id: c.id, label: c.title + (c.reference_no ? " · " + c.reference_no : "") }));
  }
  return meetings.value
    .filter((m) => ["held", "minutes_draft", "minutes_signed", "closed"].includes(m.status))
    .map((m) => ({
      id: m.id,
      label:
        (m.title || m.meeting_type) + " · " + new Date(m.scheduled_at).toLocaleDateString(),
    }));
});

function fail(e: unknown): void {
  error.value = e as ErrorEnvelope;
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    ctcs.value = (
      await api.get<ListResponse<Ctc>>("/secretarial/ctcs", {
        params: { entity_id: store.entityId, page_size: 100 },
      })
    ).data.items;
    meetings.value = (
      await api.get<ListResponse<Meeting>>("/secretarial/meetings", {
        params: { entity_id: store.entityId, page_size: 100 },
      })
    ).data.items;
    circulars.value = (
      await api.get<ListResponse<Circular>>("/secretarial/circulars", {
        params: { entity_id: store.entityId, page_size: 100 },
      })
    ).data.items;
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

async function prefill(): Promise<void> {
  if (!sourceId.value) return;
  busy.value = "prefill";
  error.value = null;
  try {
    const params: Record<string, string> = { passage_mode: passageMode.value };
    if (usesCircular.value) params.circular_id = sourceId.value;
    else params.meeting_id = sourceId.value;

    const data = (await api.get<Record<string, any>>("/secretarial/ctcs/prefill", { params })).data;
    resolutionText.value = data.resolution_text ?? "";
    passedOn.value = data.passed_on ?? "";
    available.value = data.available_signatories ?? [];
    chosen.value = available.value.slice(0, 2).map((s) => s.person_id);
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function issue(): Promise<void> {
  if (!store.entityId) return;
  busy.value = "issue";
  error.value = null;
  try {
    const signatories = available.value
      .filter((s) => chosen.value.includes(s.person_id))
      .map((s) => ({ name: s.name, designation: s.designation, din: s.din || null }));

    await api.post("/secretarial/ctcs", {
      entity_id: store.entityId,
      passage_mode: passageMode.value,
      resolution_text: resolutionText.value,
      signatories,
      passed_on: passedOn.value || null,
      meeting_id: usesCircular.value ? null : sourceId.value || null,
      circular_id: usesCircular.value ? sourceId.value || null : null,
      place: place.value || null,
      issued_to: issuedTo.value || null,
      purpose: purpose.value || null,
    });
    composing.value = false;
    resolutionText.value = "";
    sourceId.value = "";
    chosen.value = [];
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

watch(() => store.entityId, load);
watch(passageMode, () => {
  sourceId.value = "";
  resolutionText.value = "";
  available.value = [];
});
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Certified true copies</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          The register of certified copies issued. Append-only: a correction is a new copy that
          names the one it replaces and the reason.
        </p>
      </div>
      <button
        class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        @click="composing = !composing"
      >
        {{ composing ? "Cancel" : "Issue a copy" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <section v-if="composing" class="mb-4 space-y-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div class="grid gap-3 sm:grid-cols-3">
        <label class="text-xs text-gray-500">
          Passed by
          <select v-model="passageMode" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option v-for="m in MODES" :key="m.key" :value="m.key">{{ m.label }}</option>
          </select>
        </label>
        <label class="text-xs text-gray-500 sm:col-span-2">
          Source record
          <div class="mt-1 flex gap-2">
            <select v-model="sourceId" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
              <option value="">Select…</option>
              <option v-for="o in sourceOptions" :key="o.id" :value="o.id">{{ o.label }}</option>
            </select>
            <button
              class="shrink-0 rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              :disabled="!sourceId || busy !== ''"
              @click="prefill"
            >
              {{ busy === "prefill" ? "…" : "Prefill" }}
            </button>
          </div>
        </label>
      </div>

      <label class="block text-xs text-gray-500">
        Resolution text
        <textarea
          v-model="resolutionText"
          rows="5"
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
          placeholder="Prefill from the source record above rather than typing this by hand."
        ></textarea>
      </label>

      <div v-if="available.length">
        <p class="mb-1 text-xs uppercase tracking-wide text-gray-400">Signatories</p>
        <label
          v-for="s in available"
          :key="s.person_id"
          class="mr-4 inline-flex items-center gap-1 text-sm text-gray-700"
        >
          <input v-model="chosen" type="checkbox" :value="s.person_id" />
          {{ s.name }}
          <span class="text-xs text-gray-400">({{ s.designation }}<span v-if="s.din"> · DIN {{ s.din }}</span>)</span>
        </label>
      </div>

      <div class="grid gap-3 sm:grid-cols-4">
        <label class="text-xs text-gray-500">
          Passed on
          <input v-model="passedOn" type="date" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Place
          <input v-model="place" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Issued to
          <input
            v-model="issuedTo"
            placeholder="e.g. HDFC Bank Ltd"
            class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
          />
        </label>
        <label class="text-xs text-gray-500">
          Purpose
          <input v-model="purpose" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
      </div>

      <button
        class="rounded bg-gray-900 px-4 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
        :disabled="busy !== '' || !resolutionText || !chosen.length"
        @click="issue"
      >
        {{ busy === "issue" ? "Issuing…" : "Issue certified copy" }}
      </button>
    </section>

    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div v-else class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">No.</th>
            <th class="px-3 py-2">Resolution</th>
            <th class="px-3 py-2">Passed</th>
            <th class="px-3 py-2">Issued to</th>
            <th class="px-3 py-2">Certified</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="c in ctcs" :key="c.id" class="hover:bg-gray-50">
            <td class="px-3 py-2 font-mono text-xs text-gray-500">#{{ c.issuance_no }}</td>
            <td class="max-w-md px-3 py-2">
              <p class="truncate text-gray-900">{{ c.resolution_text }}</p>
              <p v-if="c.supersedes_id" class="text-xs text-amber-700">
                Supersedes an earlier copy — {{ c.superseded_reason }}
              </p>
            </td>
            <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">
              {{ c.passage_mode }}<span v-if="c.passed_on"> · {{ c.passed_on }}</span>
            </td>
            <td class="px-3 py-2 text-xs text-gray-500">{{ c.issued_to || "—" }}</td>
            <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">{{ c.certified_on }}</td>
            <td class="px-3 py-2 text-right">
              <button
                v-if="c.document_id"
                class="text-xs text-blue-600 hover:underline"
                @click="openPdf(`/secretarial/documents/${c.document_id}/download?fmt=pdf`)"
              >
                PDF
              </button>
            </td>
          </tr>
          <tr v-if="!ctcs.length">
            <td colspan="6" class="px-3 py-8 text-center text-sm text-gray-400">
              No certified copies issued yet.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
