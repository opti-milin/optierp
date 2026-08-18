<script setup lang="ts">
// One meeting, end to end: the SS-1/SS-2 clock, the agenda, who turned up, the papers
// generated from that agenda, and the evidence that each director received them.
//
// It is one page rather than a wizard because a secretary works a meeting in passes —
// draft the agenda, come back, record attendance, come back — and a wizard would make
// each return trip a navigation problem.
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api, openPdf } from "@/api/client";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  AgendaItem,
  Appointment,
  Attendance,
  CirculationRecipient,
  Meeting,
  SecretarialDocument,
} from "@/types/secretarial";

const props = defineProps<{ id: string }>();

const meeting = ref<Meeting | null>(null);
const panel = ref<Record<string, any> | null>(null);
const agenda = ref<AgendaItem[]>([]);
const attendance = ref<Attendance[]>([]);
const appointments = ref<Appointment[]>([]);
const documents = ref<SecretarialDocument[]>([]);
const circulationId = ref<string | null>(null);
const recipients = ref<CirculationRecipient[]>([]);

const loading = ref(true);
const error = ref<ErrorEnvelope | null>(null);
const busy = ref("");

// Draft rows the user is editing; only written back when they press Save.
const draftAgenda = ref<{ title: string; body: string; resolution_text: string; source: string; circular_id: string | null }[]>([]);
const draftAttendance = ref<Record<string, { status: string; is_chairperson: boolean }>>({});

const FLOW = ["draft", "scheduled", "circulated", "held", "minutes_draft", "minutes_signed", "closed"];

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

const PANEL_TONE: Record<string, string> = {
  ok: "text-green-700",
  pending: "text-gray-600",
  breach: "text-red-700",
  short: "text-amber-700",
  overdue: "text-red-700",
};

const nextStatus = computed(() => {
  const i = FLOW.indexOf(meeting.value?.status ?? "");
  return i >= 0 && i < FLOW.length - 1 ? FLOW[i + 1] : null;
});

// Only directors and partners in office attend; a ceased one must not appear in a
// sheet that will be signed.
const eligibleAttendees = computed(() =>
  appointments.value.filter(
    (a) => !a.ceased_on && ["director", "designated_partner", "partner"].includes(a.role_type),
  ),
);

function fail(e: unknown): void {
  error.value = e as ErrorEnvelope;
}

async function loadAll(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    meeting.value = (await api.get<Meeting>(`/secretarial/meetings/${props.id}`)).data;
    panel.value = (await api.get(`/secretarial/meetings/${props.id}/compliance`)).data;
    agenda.value = (await api.get<AgendaItem[]>(`/secretarial/meetings/${props.id}/agenda`)).data;
    attendance.value = (await api.get<Attendance[]>(`/secretarial/meetings/${props.id}/attendance`)).data;

    appointments.value = (
      await api.get<Appointment[]>(`/secretarial/entities/${meeting.value.entity_id}/appointments`)
    ).data;

    documents.value = (
      await api.get<ListResponse<SecretarialDocument>>("/secretarial/documents", {
        params: { source_doctype: "Secretarial Meeting", source_id: props.id, page_size: 50 },
      })
    ).data.items;

    syncDrafts();
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

function syncDrafts(): void {
  draftAgenda.value = agenda.value.map((a) => ({
    title: a.title,
    body: a.body ?? "",
    resolution_text: a.resolution_text ?? "",
    source: a.source,
    circular_id: a.circular_id,
  }));
  const map: Record<string, { status: string; is_chairperson: boolean }> = {};
  for (const person of eligibleAttendees.value) {
    const existing = attendance.value.find((r) => r.person_id === person.person_id);
    map[person.person_id] = {
      status: existing?.status ?? "present",
      is_chairperson: existing?.is_chairperson ?? false,
    };
  }
  draftAttendance.value = map;
}

async function transition(target: string): Promise<void> {
  const body: Record<string, unknown> = { target };
  if (target === "minutes_signed") {
    const pages = window.prompt("How many pages does this minute entry occupy?", "1");
    if (!pages) return;
    body.pages = Number(pages);
  }
  busy.value = target;
  error.value = null;
  try {
    await api.post(`/secretarial/meetings/${props.id}/transition`, body);
    await loadAll();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

function addAgendaRow(): void {
  draftAgenda.value.push({ title: "", body: "", resolution_text: "", source: "manual", circular_id: null });
}

async function saveAgenda(): Promise<void> {
  busy.value = "agenda";
  error.value = null;
  try {
    // Ratification rows are inserted by the circular workflow and re-sent verbatim; the
    // server preserves them, but sending them back unchanged keeps the ordering intact.
    const payload = draftAgenda.value
      .filter((r) => r.title.trim())
      .map((r) => ({
        title: r.title,
        body: r.body || null,
        resolution_text: r.resolution_text || null,
        source: r.source,
      }));
    agenda.value = (await api.put<AgendaItem[]>(`/secretarial/meetings/${props.id}/agenda`, payload)).data;
    syncDrafts();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function saveAttendance(): Promise<void> {
  busy.value = "attendance";
  error.value = null;
  try {
    const payload = Object.entries(draftAttendance.value).map(([person_id, v]) => ({
      person_id,
      status: v.status,
      is_chairperson: v.is_chairperson,
    }));
    attendance.value = (
      await api.put<Attendance[]>(`/secretarial/meetings/${props.id}/attendance`, payload)
    ).data;
    panel.value = (await api.get(`/secretarial/meetings/${props.id}/compliance`)).data;
    meeting.value = (await api.get<Meeting>(`/secretarial/meetings/${props.id}`)).data;
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function generatePack(): Promise<void> {
  busy.value = "pack";
  error.value = null;
  try {
    documents.value = (
      await api.post<SecretarialDocument[]>(`/secretarial/meetings/${props.id}/generate-pack`, {})
    ).data;
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function circulate(): Promise<void> {
  busy.value = "circulate";
  error.value = null;
  try {
    const resp = await api.post<{ id: string }>(`/secretarial/meetings/${props.id}/circulate`, {
      document_ids: documents.value.map((d) => d.id),
      send_email: true,
    });
    circulationId.value = resp.data.id;
    await loadRecipients();
    await loadAll();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function loadRecipients(): Promise<void> {
  if (!circulationId.value) return;
  try {
    recipients.value = (
      await api.get<CirculationRecipient[]>(
        `/secretarial/meetings/circulations/${circulationId.value}/recipients`,
      )
    ).data;
  } catch (e) {
    fail(e);
  }
}

async function exportAudit(): Promise<void> {
  if (!circulationId.value) return;
  const resp = await api.get(
    `/secretarial/meetings/circulations/${circulationId.value}/audit-export`,
    { responseType: "blob" },
  );
  const url = URL.createObjectURL(resp.data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "circulation-evidence.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

watch(() => props.id, loadAll, { immediate: true });
</script>

<template>
  <div v-if="meeting">
    <RouterLink to="/secretarial/meetings" class="text-xs text-gray-400 hover:underline">← Meetings</RouterLink>

    <div class="mb-4 mt-1 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">
          {{ meeting.title || meeting.meeting_type }}
          <span v-if="meeting.serial_no" class="text-gray-400">#{{ meeting.serial_no }}</span>
        </h1>
        <p class="text-sm text-gray-500">
          {{ new Date(meeting.scheduled_at).toLocaleString() }}
          <span v-if="meeting.venue"> · {{ meeting.venue }}</span> · FY {{ meeting.fy }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span class="rounded px-2 py-1 text-xs" :class="STATUS_TONE[meeting.status]">
          {{ meeting.status.replace("_", " ") }}
        </span>
        <button
          v-if="nextStatus"
          class="rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
          :disabled="busy !== ''"
          @click="transition(nextStatus)"
        >
          {{ busy === nextStatus ? "Working…" : "Mark " + nextStatus.replace("_", " ") }}
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <!-- SS-1 / SS-2 panel: the statutory clock, computed rather than remembered. -->
    <div v-if="panel" class="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p class="text-[11px] uppercase tracking-wide text-gray-400">Notice</p>
        <p class="text-sm font-medium" :class="PANEL_TONE[panel.notice?.status] ?? 'text-gray-700'">
          {{ panel.notice?.status }}
        </p>
        <p class="mt-1 text-xs text-gray-500">{{ panel.notice?.message }}</p>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p class="text-[11px] uppercase tracking-wide text-gray-400">Minutes</p>
        <p class="text-sm font-medium" :class="PANEL_TONE[panel.minutes?.status] ?? 'text-gray-700'">
          {{ panel.minutes?.status }}
        </p>
        <p class="mt-1 text-xs text-gray-500">{{ panel.minutes?.message }}</p>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p class="text-[11px] uppercase tracking-wide text-gray-400">Minutes book</p>
        <p class="text-sm font-medium text-gray-700">
          <span v-if="panel.minutes_book?.allocated_entry_no">
            Entry {{ panel.minutes_book.allocated_entry_no }} · pp. {{ panel.minutes_book.allocated_pages }}
          </span>
          <span v-else class="text-gray-400">not allocated</span>
        </p>
        <p class="mt-1 text-xs text-gray-500">
          Next free: entry {{ panel.minutes_book?.next_entry_no }}, page {{ panel.minutes_book?.next_page_no }}
        </p>
      </div>
      <div class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p class="text-[11px] uppercase tracking-wide text-gray-400">Quorum</p>
        <p
          class="text-sm font-medium"
          :class="panel.quorum?.met === true ? 'text-green-700' : panel.quorum?.met === false ? 'text-red-700' : 'text-gray-400'"
        >
          {{ panel.quorum?.met === null ? "not recorded" : panel.quorum?.met ? "met" : "not met" }}
        </p>
        <p v-if="panel.gap_warning" class="mt-1 text-xs text-amber-700">{{ panel.gap_warning }}</p>
      </div>
    </div>

    <div class="grid gap-4 lg:grid-cols-2">
      <!-- Agenda -->
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900">Agenda</h2>
          <div class="flex gap-2">
            <button class="text-xs text-blue-600 hover:underline" @click="addAgendaRow">+ Add item</button>
            <button
              class="rounded bg-gray-900 px-2 py-1 text-xs text-white disabled:opacity-50"
              :disabled="busy !== ''"
              @click="saveAgenda"
            >
              {{ busy === "agenda" ? "Saving…" : "Save agenda" }}
            </button>
          </div>
        </div>

        <p v-if="!draftAgenda.length" class="py-6 text-center text-sm text-gray-400">
          No items yet. The notice, minutes and attendance sheet all render from this list.
        </p>

        <div v-for="(row, i) in draftAgenda" :key="i" class="mb-3 border-b border-gray-100 pb-3 last:border-0">
          <div class="flex items-start gap-2">
            <span class="mt-2 w-5 shrink-0 text-xs text-gray-400">{{ i + 1 }}.</span>
            <div class="min-w-0 flex-1">
              <input
                v-model="row.title"
                placeholder="Item title"
                class="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                :readonly="row.source === 'ratification'"
              />
              <textarea
                v-model="row.resolution_text"
                rows="2"
                placeholder="Resolution text (optional)"
                class="mt-1 w-full rounded border border-gray-200 px-2 py-1 text-xs"
              ></textarea>
              <p v-if="row.source === 'ratification'" class="mt-1 text-[11px] text-indigo-600">
                Added automatically to ratify a circular resolution — kept even if you rewrite the agenda.
              </p>
            </div>
            <button
              v-if="row.source !== 'ratification'"
              class="mt-2 shrink-0 text-xs text-gray-400 hover:text-red-600"
              @click="draftAgenda.splice(i, 1)"
            >
              ✕
            </button>
          </div>
        </div>
      </section>

      <!-- Attendance -->
      <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900">Attendance</h2>
          <button
            class="rounded bg-gray-900 px-2 py-1 text-xs text-white disabled:opacity-50"
            :disabled="busy !== ''"
            @click="saveAttendance"
          >
            {{ busy === "attendance" ? "Saving…" : "Save attendance" }}
          </button>
        </div>

        <p v-if="!eligibleAttendees.length" class="py-6 text-center text-sm text-gray-400">
          No directors or partners in office for this entity.
        </p>

        <table v-else class="min-w-full text-sm">
          <tbody class="divide-y divide-gray-100">
            <tr v-for="a in eligibleAttendees" :key="a.person_id">
              <td class="py-2 pr-2">
                <p class="text-gray-900">{{ a.person_name }}</p>
                <p class="text-xs text-gray-400">{{ a.role_type.replace("_", " ") }}</p>
              </td>
              <td class="py-2">
                <select
                  v-model="draftAttendance[a.person_id].status"
                  class="rounded border border-gray-300 px-2 py-1 text-xs"
                >
                  <option value="present">Present</option>
                  <option value="video">Via video</option>
                  <option value="leave_of_absence">Leave of absence</option>
                  <option value="absent">Absent</option>
                </select>
              </td>
              <td class="py-2 pl-2 text-xs text-gray-500">
                <label class="flex items-center gap-1">
                  <input v-model="draftAttendance[a.person_id].is_chairperson" type="checkbox" />
                  chair
                </label>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- Papers and evidence -->
    <section class="mt-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 class="text-sm font-semibold text-gray-900">Papers</h2>
          <p class="text-xs text-gray-500">
            Notice, minutes and the attendance sheet all render from the agenda above, which is
            what keeps their wording and numbering identical.
          </p>
        </div>
        <div class="flex gap-2">
          <button
            class="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            :disabled="busy !== ''"
            @click="generatePack"
          >
            {{ busy === "pack" ? "Generating…" : "Generate pack" }}
          </button>
          <button
            class="rounded bg-gray-900 px-3 py-1.5 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
            :disabled="busy !== '' || !documents.length"
            @click="circulate"
          >
            {{ busy === "circulate" ? "Sending…" : "Circulate to participants" }}
          </button>
        </div>
      </div>

      <ul v-if="documents.length" class="divide-y divide-gray-100">
        <li v-for="d in documents" :key="d.id" class="flex items-center justify-between py-2">
          <div>
            <p class="text-sm text-gray-900">{{ d.title }}</p>
            <p class="text-xs text-gray-400">
              {{ d.document_type }} · v{{ d.version }} · {{ d.status }}
            </p>
          </div>
          <button
            class="text-xs text-blue-600 hover:underline"
            @click="openPdf(`/secretarial/documents/${d.id}/download?fmt=pdf`)"
          >
            PDF
          </button>
        </li>
      </ul>
      <p v-else class="py-4 text-center text-sm text-gray-400">Nothing generated yet.</p>

      <div v-if="recipients.length" class="mt-4">
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-xs uppercase tracking-wide text-gray-400">Who has opened the papers</h3>
          <button class="text-xs text-blue-600 hover:underline" @click="exportAudit">Export evidence CSV</button>
        </div>
        <table class="min-w-full text-sm">
          <tbody class="divide-y divide-gray-100">
            <tr v-for="r in recipients" :key="r.id">
              <td class="py-2 text-gray-900">{{ r.person_name }}</td>
              <td class="py-2 text-xs text-gray-500">{{ r.email || "no email on record" }}</td>
              <td class="py-2 text-xs">
                <span
                  class="rounded px-1.5 py-0.5"
                  :class="
                    r.status === 'acknowledged'
                      ? 'bg-green-100 text-green-800'
                      : r.status === 'viewed'
                        ? 'bg-blue-100 text-blue-800'
                        : 'bg-gray-100 text-gray-600'
                  "
                >
                  {{ r.status }}
                </span>
              </td>
              <td class="py-2 text-xs text-gray-400">
                {{ r.acknowledged_at || r.viewed_at || r.sent_at || "" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>

  <p v-else-if="loading" class="text-sm text-gray-500">Loading…</p>
  <p v-else class="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
    {{ error?.detail ?? "Meeting not found." }}
  </p>
</template>
