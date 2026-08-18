<script setup lang="ts">
// One circular resolution, from draft to ratification.
//
// Two things on this page carry the weight. The Rule 5 gate runs *before* circulating,
// because a resolution on a restricted matter is void and nobody finds out until it
// matters. And the tally is read from the server on every refresh rather than counted
// in the browser — the outcome is a legal fact, not a UI state.
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type {
  Appointment,
  CircularDetail,
  CircularTally,
  ConsentResponse,
  Eligibility,
} from "@/types/secretarial";

const props = defineProps<{ id: string }>();

const circular = ref<CircularDetail | null>(null);
const tally = ref<CircularTally | null>(null);
const responses = ref<ConsentResponse[]>([]);
const eligibility = ref<Eligibility | null>(null);
const appointments = ref<Appointment[]>([]);

const loading = ref(true);
const error = ref<ErrorEnvelope | null>(null);
const busy = ref("");
const showCirculate = ref(false);
const interested = ref<string[]>([]);
const expiresAt = ref("");

const STATUS_TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  circulating: "bg-blue-100 text-blue-800",
  passed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  expired: "bg-gray-200 text-gray-500",
  ratified: "bg-green-100 text-green-800",
};

const RESPONSE_TONE: Record<string, string> = {
  pending: "bg-gray-100 text-gray-600",
  viewed: "bg-blue-100 text-blue-800",
  consented: "bg-green-100 text-green-800",
  declined: "bg-red-100 text-red-800",
  abstained: "bg-amber-100 text-amber-800",
};

const directors = computed(() =>
  appointments.value.filter((a) => !a.ceased_on && ["director", "designated_partner"].includes(a.role_type)),
);

const isDraft = computed(() => circular.value?.status === "draft");
const isDecided = computed(() =>
  ["passed", "failed", "expired", "ratified"].includes(circular.value?.status ?? ""),
);

// Progress towards the threshold, for the bar. Guarded because `needed` is 0 for an
// entity with no directors on record, and dividing by it would render NaN%.
const progress = computed(() => {
  if (!tally.value?.needed) return 0;
  return Math.min(100, Math.round((tally.value.consented / tally.value.needed) * 100));
});

function fail(e: unknown): void {
  error.value = e as ErrorEnvelope;
}

async function loadAll(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    circular.value = (await api.get<CircularDetail>(`/secretarial/circulars/${props.id}`)).data;
    appointments.value = (
      await api.get<Appointment[]>(`/secretarial/entities/${circular.value.entity_id}/appointments`)
    ).data;
    if (circular.value.status !== "draft") {
      await refreshTally();
    }
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

async function refreshTally(): Promise<void> {
  tally.value = (await api.get<CircularTally>(`/secretarial/circulars/${props.id}/tally`)).data;
  responses.value = (
    await api.get<ConsentResponse[]>(`/secretarial/circulars/${props.id}/responses`)
  ).data;
}

async function checkEligibility(): Promise<void> {
  busy.value = "eligibility";
  error.value = null;
  try {
    eligibility.value = (
      await api.post<Eligibility>(`/secretarial/circulars/${props.id}/check-eligibility`)
    ).data;
    if (eligibility.value.eligible) showCirculate.value = true;
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function circulate(override = false): Promise<void> {
  let overrideReason: string | null = null;
  if (override) {
    overrideReason = window.prompt(
      "Rule 5 says this matter must be decided at a meeting.\n\n" +
        "Overriding is recorded against the resolution with your reason. Why are you proceeding?",
    );
    if (!overrideReason) return;
  }

  busy.value = "circulate";
  error.value = null;
  try {
    await api.post(`/secretarial/circulars/${props.id}/circulate`, {
      interested_person_ids: interested.value,
      expires_at: expiresAt.value ? new Date(expiresAt.value).toISOString() : null,
      override_rule5: override,
      override_reason: overrideReason,
      send_email: true,
    });
    showCirculate.value = false;
    await loadAll();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function refreshOutcome(): Promise<void> {
  busy.value = "refresh";
  try {
    circular.value = (await api.post<CircularDetail>(`/secretarial/circulars/${props.id}/refresh`)).data;
    await refreshTally();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function ratify(): Promise<void> {
  busy.value = "ratify";
  error.value = null;
  try {
    await api.post(`/secretarial/circulars/${props.id}/ratify`, {});
    await loadAll();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

watch(() => props.id, loadAll, { immediate: true });
</script>

<template>
  <div v-if="circular">
    <RouterLink to="/secretarial/circulars" class="text-xs text-gray-400 hover:underline">
      ← Circular resolutions
    </RouterLink>

    <div class="mb-4 mt-1 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">{{ circular.title }}</h1>
        <p class="text-sm text-gray-500">
          <span v-if="circular.reference_no" class="font-mono text-xs">{{ circular.reference_no }} · </span>
          Passes on {{ circular.consent_rule.replace("_", " ") }}
        </p>
      </div>
      <span class="rounded px-2 py-1 text-xs" :class="STATUS_TONE[circular.status]">{{ circular.status }}</span>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="grid gap-4 lg:grid-cols-3">
      <div class="space-y-4 lg:col-span-2">
        <section class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h2 class="mb-2 text-xs uppercase tracking-wide text-gray-400">Resolution</h2>
          <p v-if="circular.description" class="mb-3 whitespace-pre-line text-sm text-gray-600">
            {{ circular.description }}
          </p>
          <blockquote
            class="whitespace-pre-line border-l-4 border-gray-300 bg-gray-50 px-4 py-3 text-sm leading-relaxed text-gray-800"
          >
            {{ circular.resolution_text }}
          </blockquote>
        </section>

        <!-- The Rule 5 gate. Shown only while it can still change the outcome. -->
        <section v-if="isDraft" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-2 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900">Before circulating</h2>
            <button
              class="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              :disabled="busy !== ''"
              @click="checkEligibility"
            >
              {{ busy === "eligibility" ? "Checking…" : "Check Rule 5" }}
            </button>
          </div>
          <p class="text-xs text-gray-500">
            Rule 5 of the Companies (Meetings of Board and its Powers) Rules, 2014 reserves certain
            matters to a meeting. A resolution passed by circulation on one of them is void.
          </p>

          <div
            v-if="eligibility && !eligibility.eligible"
            class="mt-3 rounded border border-red-200 bg-red-50 p-3"
          >
            <p class="text-sm font-medium text-red-800">{{ eligibility.message }}</p>
            <ul class="mt-2 space-y-1">
              <li v-for="(m, i) in eligibility.blocked_matters" :key="i" class="text-xs text-red-700">
                <span class="font-medium">{{ m.matter }}</span>
                <span class="text-red-500"> — {{ m.reference }}</span>
                <span class="block text-red-400">matched on “{{ m.matched }}”</span>
              </li>
            </ul>
            <div class="mt-3 flex gap-2">
              <button
                class="rounded border border-red-300 px-3 py-1.5 text-xs text-red-700 hover:bg-red-100"
                :disabled="busy !== ''"
                @click="circulate(true)"
              >
                Circulate anyway, with a recorded reason
              </button>
            </div>
          </div>

          <p
            v-else-if="eligibility && eligibility.eligible"
            class="mt-3 rounded border border-green-200 bg-green-50 p-3 text-sm text-green-800"
          >
            {{ eligibility.message }}
          </p>
        </section>

        <!-- Circulate form -->
        <section v-if="isDraft && showCirculate" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h2 class="mb-3 text-sm font-semibold text-gray-900">Send for consent</h2>
          <p class="mb-3 text-xs text-gray-500">
            Every director in office gets a personal link. Anyone marked interested under s.184 is
            excluded from the count entirely — not merely barred from voting.
          </p>

          <fieldset class="mb-3">
            <legend class="mb-1 text-xs uppercase tracking-wide text-gray-400">
              Interested directors (s.184)
            </legend>
            <label
              v-for="d in directors"
              :key="d.person_id"
              class="mr-4 inline-flex items-center gap-1 text-sm text-gray-700"
            >
              <input v-model="interested" type="checkbox" :value="d.person_id" />
              {{ d.person_name }}
            </label>
            <p v-if="!directors.length" class="text-sm text-gray-400">No directors in office.</p>
          </fieldset>

          <label class="mb-3 block text-xs text-gray-500">
            Closes at (optional)
            <input
              v-model="expiresAt"
              type="datetime-local"
              class="mt-1 block rounded border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>

          <button
            class="rounded bg-gray-900 px-4 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
            :disabled="busy !== ''"
            @click="circulate(false)"
          >
            {{ busy === "circulate" ? "Sending…" : "Circulate now" }}
          </button>
        </section>

        <!-- Responses -->
        <section v-if="responses.length" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <div class="mb-3 flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900">Responses</h2>
            <button
              class="text-xs text-blue-600 hover:underline disabled:opacity-50"
              :disabled="busy !== ''"
              @click="refreshOutcome"
            >
              {{ busy === "refresh" ? "Refreshing…" : "Refresh" }}
            </button>
          </div>
          <table class="min-w-full text-sm">
            <tbody class="divide-y divide-gray-100">
              <tr v-for="r in responses" :key="r.id">
                <td class="py-2">
                  <span class="text-gray-900">{{ r.person_name }}</span>
                  <span v-if="r.is_interested" class="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800">
                    interested — excluded
                  </span>
                </td>
                <td class="py-2">
                  <span class="rounded px-1.5 py-0.5 text-xs" :class="RESPONSE_TONE[r.status]">{{ r.status }}</span>
                </td>
                <td class="py-2 text-xs text-gray-400">
                  {{ r.responded_at ? new Date(r.responded_at).toLocaleString() : r.viewed_at ? "opened" : "" }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="responses.some((r) => r.comments)" class="mt-3 text-xs text-gray-500">
            <span v-for="r in responses.filter((x) => x.comments)" :key="r.id" class="block">
              <strong>{{ r.person_name }}:</strong> {{ r.comments }}
            </span>
          </p>
        </section>
      </div>

      <!-- Tally -->
      <div class="space-y-4">
        <section v-if="tally" class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h2 class="mb-3 text-xs uppercase tracking-wide text-gray-400">Tally</h2>

          <div class="mb-3">
            <div class="flex items-baseline justify-between">
              <p class="text-2xl font-semibold text-gray-900">
                {{ tally.consented }}<span class="text-base text-gray-400">/{{ tally.needed }}</span>
              </p>
              <p class="text-xs text-gray-500">needed to pass</p>
            </div>
            <div class="mt-2 h-2 w-full overflow-hidden rounded bg-gray-100">
              <div
                class="h-full rounded"
                :class="tally.reached ? 'bg-green-500' : tally.impossible ? 'bg-red-400' : 'bg-blue-500'"
                :style="{ width: progress + '%' }"
              ></div>
            </div>
          </div>

          <dl class="space-y-1 text-sm">
            <div class="flex justify-between"><dt class="text-gray-500">Entitled</dt><dd>{{ tally.entitled }}</dd></div>
            <div v-if="tally.interested_excluded" class="flex justify-between">
              <dt class="text-amber-700">Excluded (s.184)</dt><dd>{{ tally.interested_excluded }}</dd>
            </div>
            <div class="flex justify-between"><dt class="text-green-700">Consented</dt><dd>{{ tally.consented }}</dd></div>
            <div class="flex justify-between"><dt class="text-red-700">Declined</dt><dd>{{ tally.declined }}</dd></div>
            <div class="flex justify-between"><dt class="text-amber-700">Abstained</dt><dd>{{ tally.abstained }}</dd></div>
            <div class="flex justify-between"><dt class="text-gray-500">Pending</dt><dd>{{ tally.pending }}</dd></div>
          </dl>

          <p v-if="tally.impossible" class="mt-3 rounded bg-red-50 p-2 text-xs text-red-700">
            Enough directors have declined that the threshold can no longer be reached.
          </p>
          <p v-if="tally.expires_at" class="mt-2 text-xs text-gray-400">
            Closes {{ new Date(tally.expires_at).toLocaleString() }}
          </p>
        </section>

        <!-- Ratification -->
        <section
          v-if="isDecided && circular.status !== 'ratified'"
          class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
        >
          <h2 class="mb-1 text-sm font-semibold text-gray-900">Ratification</h2>
          <p class="mb-3 text-xs text-gray-500">
            A circular resolution is noted at the next board meeting. This adds the item to that
            agenda — creating the meeting if none is scheduled.
          </p>
          <button
            class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
            :disabled="busy !== ''"
            @click="ratify"
          >
            {{ busy === "ratify" ? "Working…" : "Place on the next board agenda" }}
          </button>
        </section>

        <section
          v-if="circular.ratified_meeting_id"
          class="rounded-lg border border-green-200 bg-green-50 p-4 text-sm text-green-800 shadow-sm"
        >
          Ratified at
          <RouterLink
            :to="'/secretarial/meetings/' + circular.ratified_meeting_id"
            class="underline"
          >
            this board meeting
          </RouterLink>
          .
        </section>
      </div>
    </div>
  </div>

  <p v-else-if="loading" class="text-sm text-gray-500">Loading…</p>
  <p v-else class="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
    {{ error?.detail ?? "Resolution not found." }}
  </p>
</template>
