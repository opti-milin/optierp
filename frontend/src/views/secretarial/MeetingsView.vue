<script setup lang="ts">
// The meeting register — every board, general and committee meeting for the working entity.
//
// Sorted newest first because the question people arrive with is "what happened at the
// last board meeting", not "what happened in 2019".
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { Meeting } from "@/types/secretarial";

const store = useSecretarialStore();

const meetings = ref<Meeting[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const typeFilter = ref("");
const statusFilter = ref("");
const creating = ref(false);

const form = ref({
  meeting_type: "board",
  scheduled_at: "",
  venue: "",
  title: "",
  mode: "physical",
});

const TYPES = [
  { key: "board", label: "Board meeting" },
  { key: "agm", label: "AGM" },
  { key: "egm", label: "EGM" },
  { key: "committee", label: "Committee" },
  { key: "partners", label: "Partners" },
];

const STATUS_TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  scheduled: "bg-blue-100 text-blue-800",
  circulated: "bg-indigo-100 text-indigo-800",
  held: "bg-amber-100 text-amber-800",
  minutes_draft: "bg-amber-100 text-amber-800",
  minutes_signed: "bg-green-100 text-green-800",
  closed: "bg-green-100 text-green-800",
  cancelled: "bg-gray-200 text-gray-500",
};

// An LLP holds partners meetings, not board meetings. Offering both invites the wrong pick.
const availableTypes = computed(() =>
  store.entity?.kind === "llp"
    ? TYPES.filter((t) => ["partners", "committee"].includes(t.key))
    : TYPES.filter((t) => t.key !== "partners"),
);

function label(key: string): string {
  return TYPES.find((t) => t.key === key)?.label ?? key;
}

function when(m: Meeting): string {
  return new Date(m.scheduled_at).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    meetings.value = (
      await api.get<ListResponse<Meeting>>("/secretarial/meetings", {
        params: {
          entity_id: store.entityId,
          meeting_type: typeFilter.value || undefined,
          status: statusFilter.value || undefined,
          page_size: 100,
        },
      })
    ).data.items;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function create(): Promise<void> {
  if (!store.entityId || !form.value.scheduled_at) return;
  error.value = null;
  try {
    await api.post("/secretarial/meetings", {
      entity_id: store.entityId,
      meeting_type: form.value.meeting_type,
      scheduled_at: new Date(form.value.scheduled_at).toISOString(),
      venue: form.value.venue || null,
      title: form.value.title || null,
      mode: form.value.mode,
    });
    creating.value = false;
    form.value = { meeting_type: "board", scheduled_at: "", venue: "", title: "", mode: "physical" };
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  }
}

watch(() => [store.entityId, typeFilter.value, statusFilter.value], load);
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Meetings</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          Board, general and committee meetings — notice periods, agendas, attendance and the
          minutes book, with the SS-1 and SS-2 dates worked out for you.
        </p>
      </div>
      <button
        class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        @click="creating = !creating"
      >
        {{ creating ? "Cancel" : "Schedule a meeting" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <form
      v-if="creating"
      class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-5"
      @submit.prevent="create"
    >
      <label class="text-xs text-gray-500">
        Type
        <select v-model="form.meeting_type" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option v-for="t in availableTypes" :key="t.key" :value="t.key">{{ t.label }}</option>
        </select>
      </label>
      <label class="text-xs text-gray-500">
        Date and time
        <input
          v-model="form.scheduled_at"
          type="datetime-local"
          required
          class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
      </label>
      <label class="text-xs text-gray-500">
        Venue
        <input v-model="form.venue" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Mode
        <select v-model="form.mode" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option value="physical">Physical</option>
          <option value="video">Video</option>
          <option value="hybrid">Hybrid</option>
        </select>
      </label>
      <div class="flex items-end">
        <button class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800">Create</button>
      </div>
    </form>

    <div class="mb-3 flex flex-wrap gap-2">
      <select v-model="typeFilter" class="rounded border border-gray-300 px-2 py-1 text-sm">
        <option value="">All types</option>
        <option v-for="t in TYPES" :key="t.key" :value="t.key">{{ t.label }}</option>
      </select>
      <select v-model="statusFilter" class="rounded border border-gray-300 px-2 py-1 text-sm">
        <option value="">All states</option>
        <option v-for="s in Object.keys(STATUS_TONE)" :key="s" :value="s">{{ s.replace("_", " ") }}</option>
      </select>
    </div>

    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div v-else class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Meeting</th>
            <th class="px-3 py-2">When</th>
            <th class="px-3 py-2">Venue</th>
            <th class="px-3 py-2">Minutes book</th>
            <th class="px-3 py-2">State</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="m in meetings" :key="m.id" class="hover:bg-gray-50">
            <td class="px-3 py-2">
              <RouterLink
                :to="'/secretarial/meetings/' + m.id"
                class="font-medium text-blue-600 hover:underline"
              >
                {{ m.title || label(m.meeting_type) }}
                <span v-if="m.serial_no" class="text-gray-400">#{{ m.serial_no }}</span>
              </RouterLink>
              <p class="text-xs text-gray-400">{{ label(m.meeting_type) }} · FY {{ m.fy }}</p>
            </td>
            <td class="whitespace-nowrap px-3 py-2 text-gray-700">{{ when(m) }}</td>
            <td class="px-3 py-2 text-gray-500">{{ m.venue || "—" }}</td>
            <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">
              <span v-if="m.minutes_entry_no">
                Entry {{ m.minutes_entry_no }} · pp. {{ m.minutes_page_from }}–{{ m.minutes_page_to }}
              </span>
              <span v-else class="text-gray-300">not yet signed</span>
            </td>
            <td class="px-3 py-2">
              <span class="rounded px-1.5 py-0.5 text-xs" :class="STATUS_TONE[m.status]">
                {{ m.status.replace("_", " ") }}
              </span>
            </td>
          </tr>
          <tr v-if="!meetings.length">
            <td colspan="5" class="px-3 py-8 text-center text-sm text-gray-400">
              No meetings yet. Schedule one to start the agenda, notice and minutes thread.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
