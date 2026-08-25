<script setup lang="ts">
// Share capital: who holds what, how it moved, and the paper that proves it.
//
// Four tabs rather than four pages because they are one story read at different
// depths — the cap table is the answer, the certificates are the evidence, the
// transfers are the history, and the events are what is about to change it. Splitting
// them across routes would make the common task (check a holding, then look at the
// certificate behind it) a navigation exercise.
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  CapitalEvent,
  CapitalEventType,
  CapTable,
  DividendCheck,
  Meeting,
  MemberRow,
  ShareCertificate,
  ShareTransfer,
} from "@/types/secretarial";

const store = useSecretarialStore();

type Tab = "cap-table" | "certificates" | "transfers" | "events";
const tab = ref<Tab>("cap-table");

const capTable = ref<CapTable | null>(null);
const certificates = ref<ShareCertificate[]>([]);
const transfers = ref<ShareTransfer[]>([]);
const events = ref<CapitalEvent[]>([]);
const members = ref<MemberRow[]>([]);
const meetings = ref<Meeting[]>([]);
const dividend = ref<DividendCheck | null>(null);

const loading = ref(false);
const busy = ref("");
const error = ref<ErrorEnvelope | null>(null);
const notice = ref("");

const certFilter = ref("issued");
const showIssue = ref(false);
const showTransfer = ref(false);
const showEvent = ref(false);

const issueForm = ref({ member_id: "", no_of_shares: "", share_class: "Equity", face_value: "" });
const transferForm = ref({
  transferor_member_id: "",
  transferee_member_id: "",
  no_of_shares: "",
  consideration: "",
  stamp_duty: "",
  executed_on: "",
});
const eventForm = ref({
  event_type: "right_issue" as CapitalEventType,
  title: "",
  shares_offered: "",
  price_per_share: "",
  face_value: "",
  offer_on: "",
});

const TRANSFER_TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  board_approved: "bg-amber-100 text-amber-800",
  issued_posted: "bg-green-100 text-green-800",
  reverted: "bg-red-100 text-red-700",
};

const CERT_TONE: Record<string, string> = {
  issued: "bg-green-100 text-green-800",
  cancelled: "bg-gray-200 text-gray-600",
  surrendered: "bg-gray-200 text-gray-600",
};

const EVENT_TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  approved: "bg-blue-100 text-blue-800",
  allotted: "bg-green-100 text-green-800",
  cancelled: "bg-red-100 text-red-700",
};

const EVENT_LABELS: Record<CapitalEventType, string> = {
  right_issue: "Right issue",
  private_placement: "Private placement",
  preferential_allotment: "Preferential allotment",
  esop_grant: "ESOP grant",
  bonus_issue: "Bonus issue",
  buyback: "Buyback",
  dividend: "Dividend",
};

// The plain-English gloss on `source`. The point of showing it at all is that a
// number backed by the ledger and a number typed in by hand deserve different trust.
const SOURCE_NOTE: Record<string, string> = {
  ledger: "Backed by the share ledger in this company's books",
  register: "From the certificate register — this client's books are kept elsewhere",
  opening: "Declared opening holdings; no certificates issued yet",
};

const num = (v: string | null | undefined): string =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-IN");

const heldMembers = computed(() => members.value.filter((m) => !m.ceased_on));

function fail(e: unknown): void {
  error.value = (e as { response?: { data?: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  const params = { entity_id: store.entityId, page_size: 200 };
  try {
    const [ct, cert, tr, ev, mem, mt] = await Promise.all([
      api.get<CapTable>("/secretarial/capital/cap-table", { params: { entity_id: store.entityId } }),
      api.get<ListResponse<ShareCertificate>>("/secretarial/capital/certificates", {
        params: { ...params, status: certFilter.value || undefined },
      }),
      api.get<ListResponse<ShareTransfer>>("/secretarial/capital/transfers", { params }),
      api.get<ListResponse<CapitalEvent>>("/secretarial/capital/events", { params }),
      api.get<ListResponse<MemberRow>>("/secretarial/registers/members", { params }),
      api.get<ListResponse<Meeting>>("/secretarial/meetings", { params }),
    ]);
    capTable.value = ct.data;
    certificates.value = cert.data.items;
    transfers.value = tr.data.items;
    events.value = ev.data.items;
    members.value = mem.data.items;
    meetings.value = mt.data.items;
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

async function reloadCertificates(): Promise<void> {
  if (!store.entityId) return;
  try {
    certificates.value = (
      await api.get<ListResponse<ShareCertificate>>("/secretarial/capital/certificates", {
        params: { entity_id: store.entityId, status: certFilter.value || undefined, page_size: 200 },
      })
    ).data.items;
  } catch (e) {
    fail(e);
  }
}

async function act(key: string, fn: () => Promise<void>): Promise<void> {
  busy.value = key;
  error.value = null;
  notice.value = "";
  try {
    await fn();
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

function issueCertificate(): Promise<void> {
  return act("issue", async () => {
    await api.post("/secretarial/capital/certificates", {
      entity_id: store.entityId,
      member_id: issueForm.value.member_id || null,
      no_of_shares: issueForm.value.no_of_shares,
      share_class: issueForm.value.share_class,
      face_value: issueForm.value.face_value || null,
    });
    showIssue.value = false;
    issueForm.value = { member_id: "", no_of_shares: "", share_class: "Equity", face_value: "" };
    notice.value = "Certificate issued; distinctive numbers allocated from the register.";
  });
}

function cancelCertificate(cert: ShareCertificate): Promise<void> {
  const reason = window.prompt(
    `Why is certificate ${cert.certificate_no} being cancelled? This is recorded permanently.`,
  );
  if (!reason) return Promise.resolve();
  return act(`cancel-${cert.id}`, async () => {
    await api.post(`/secretarial/capital/certificates/${cert.id}/cancel`, { reason });
    notice.value = `Certificate ${cert.certificate_no} cancelled and reissued over the same distinctive numbers.`;
  });
}

function lodgeTransfer(): Promise<void> {
  return act("transfer", async () => {
    await api.post("/secretarial/capital/transfers", {
      entity_id: store.entityId,
      transferor_member_id: transferForm.value.transferor_member_id || null,
      transferee_member_id: transferForm.value.transferee_member_id || null,
      no_of_shares: transferForm.value.no_of_shares,
      consideration: transferForm.value.consideration || 0,
      stamp_duty: transferForm.value.stamp_duty || null,
      executed_on: transferForm.value.executed_on,
    });
    showTransfer.value = false;
    transferForm.value = {
      transferor_member_id: "",
      transferee_member_id: "",
      no_of_shares: "",
      consideration: "",
      stamp_duty: "",
      executed_on: "",
    };
  });
}

function approveTransfer(t: ShareTransfer): Promise<void> {
  const held = meetings.value.filter((m) => !["draft", "scheduled", "circulated", "cancelled"].includes(m.status));
  if (!held.length) {
    error.value = {
      detail:
        "No held meeting to approve this against. A transfer cannot be approved by a meeting that has not happened — hold the board meeting first.",
    } as ErrorEnvelope;
    return Promise.resolve();
  }
  return act(`approve-${t.id}`, async () => {
    await api.post(`/secretarial/capital/transfers/${t.id}/approve`, {
      board_meeting_id: held[0].id,
    });
  });
}

function postTransfer(t: ShareTransfer): Promise<void> {
  // Offer the transferor's own live certificate of the right size — the normal case,
  // and the one that makes the distinctive numbers travel with the shares.
  const candidate = certificates.value.find(
    (c) =>
      c.status === "issued" &&
      c.member_id === t.transferor_member_id &&
      Number(c.no_of_shares) === Number(t.no_of_shares),
  );
  return act(`post-${t.id}`, async () => {
    await api.post(`/secretarial/capital/transfers/${t.id}/post`, {
      surrender_certificate_id: candidate?.id ?? null,
    });
    notice.value = candidate
      ? `Shares moved. Certificate ${candidate.certificate_no} cancelled, replacement issued over the same numbers.`
      : "Shares moved and a fresh certificate issued.";
  });
}

function revertTransfer(t: ShareTransfer): Promise<void> {
  const reason = window.prompt(
    `Why is instrument SH-4/${t.instrument_no} being reverted? The reason is stored permanently and shown on the register.`,
  );
  if (!reason) return Promise.resolve();
  return act(`revert-${t.id}`, async () => {
    await api.post(`/secretarial/capital/transfers/${t.id}/revert`, { reason });
  });
}

function createEvent(): Promise<void> {
  return act("event", async () => {
    await api.post("/secretarial/capital/events", {
      entity_id: store.entityId,
      event_type: eventForm.value.event_type,
      title: eventForm.value.title,
      shares_offered: eventForm.value.shares_offered || null,
      price_per_share: eventForm.value.price_per_share || null,
      face_value: eventForm.value.face_value || null,
      offer_on: eventForm.value.offer_on || null,
      share_class: "Equity",
    });
    showEvent.value = false;
    eventForm.value = {
      event_type: "right_issue",
      title: "",
      shares_offered: "",
      price_per_share: "",
      face_value: "",
      offer_on: "",
    };
  });
}

function approveEvent(e: CapitalEvent): Promise<void> {
  return act(`approve-event-${e.id}`, async () => {
    await api.post(`/secretarial/capital/events/${e.id}/approve`);
  });
}

function allotEvent(e: CapitalEvent): Promise<void> {
  return act(`allot-${e.id}`, async () => {
    const resp = await api.post<{ certificates: ShareCertificate[] }>(
      `/secretarial/capital/events/${e.id}/allot`,
      { allotted_on: new Date().toISOString().slice(0, 10) },
    );
    notice.value = `Allotted. ${resp.data.certificates.length} certificate(s) issued.`;
  });
}

async function runDividendCheck(): Promise<void> {
  if (!store.entityId) return;
  busy.value = "dividend";
  error.value = null;
  try {
    dividend.value = (
      await api.get<DividendCheck>("/secretarial/capital/dividend-check", {
        params: { entity_id: store.entityId },
      })
    ).data;
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

watch(() => store.entityId, load);
watch(certFilter, reloadCertificates);
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Share capital</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        The cap table, the certificates behind it, the instruments that moved the shares, and the
        offers that are about to change it.
      </p>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="notice" class="mb-3 rounded border border-green-200 bg-green-50 p-3 text-sm text-green-800">
      {{ notice }}
    </p>

    <nav class="mb-4 flex gap-1 border-b border-gray-200 text-sm">
      <button
        v-for="t in (['cap-table', 'certificates', 'transfers', 'events'] as Tab[])"
        :key="t"
        class="-mb-px border-b-2 px-3 py-2 capitalize"
        :class="tab === t ? 'border-gray-900 font-medium text-gray-900' : 'border-transparent text-gray-500 hover:text-gray-700'"
        @click="tab = t"
      >
        {{ t.replace("-", " ") }}
      </button>
    </nav>

    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <!-- Cap table ------------------------------------------------------------- -->
    <section v-else-if="tab === 'cap-table'">
      <div v-if="capTable" class="mb-3 rounded border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900">
        <strong>{{ SOURCE_NOTE[capTable.source] }}.</strong>
        <span v-if="capTable.unissued_from">
          Next unissued distinctive number: {{ capTable.unissued_from }}.
        </span>
        <p v-for="(n, i) in capTable.notes" :key="i" class="mt-1">{{ n }}</p>
      </div>

      <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full divide-y divide-gray-200 text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th class="px-3 py-2">Holder</th>
              <th class="px-3 py-2">Folio</th>
              <th class="px-3 py-2">Class</th>
              <th class="px-3 py-2 text-right">Shares</th>
              <th class="px-3 py-2 text-right">%</th>
              <th class="px-3 py-2">Distinctive numbers</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr v-for="(r, i) in capTable?.rows ?? []" :key="i" class="hover:bg-gray-50">
              <td class="px-3 py-2 font-medium text-gray-900">{{ r.holder_name }}</td>
              <td class="px-3 py-2 text-xs text-gray-500">{{ r.folio_no || "—" }}</td>
              <td class="px-3 py-2 text-xs text-gray-500">{{ r.share_class }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ num(r.shares) }}</td>
              <td class="px-3 py-2 text-right text-xs text-gray-600">{{ r.pct ?? "—" }}</td>
              <td class="px-3 py-2 font-mono text-xs text-gray-500">
                {{ r.distinctive_ranges.join(", ") || "—" }}
              </td>
            </tr>
            <tr v-if="!capTable?.rows.length">
              <td colspan="6" class="px-3 py-8 text-center text-sm text-gray-400">
                No holdings recorded yet.
              </td>
            </tr>
          </tbody>
          <tfoot v-if="capTable?.rows.length" class="bg-gray-50 text-sm font-medium">
            <tr>
              <td class="px-3 py-2" colspan="3">Total issued</td>
              <td class="px-3 py-2 text-right font-mono">{{ num(capTable.total_shares) }}</td>
              <td colspan="2"></td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div class="mt-5 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div class="flex items-start justify-between gap-3">
          <div>
            <h2 class="text-sm font-semibold text-gray-900">Dividend — distributable profit (s.123)</h2>
            <p class="text-xs text-gray-500">
              Profit for the year plus accumulated profits, less accumulated losses.
            </p>
          </div>
          <button
            class="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50"
            :disabled="busy === 'dividend'"
            @click="runDividendCheck"
          >
            Run the check
          </button>
        </div>

        <div v-if="dividend" class="mt-3 text-sm">
          <div class="flex flex-wrap gap-4">
            <div><span class="text-xs text-gray-500">Profit for the year</span><br />{{ num(dividend.current_profit) }}</div>
            <div><span class="text-xs text-gray-500">Accumulated profits</span><br />{{ num(dividend.accumulated_profit) }}</div>
            <div><span class="text-xs text-gray-500">Distributable</span><br /><strong>{{ num(dividend.distributable) }}</strong></div>
            <div>
              <span class="text-xs text-gray-500">Verdict</span><br />
              <span
                class="rounded px-1.5 py-0.5 text-xs"
                :class="{
                  'bg-green-100 text-green-800': dividend.verdict === 'ok',
                  'bg-red-100 text-red-700': dividend.verdict === 'exceeded',
                  'bg-amber-100 text-amber-800': dividend.verdict === 'unknown',
                }"
              >{{ dividend.verdict }}</span>
            </div>
          </div>
          <ul class="mt-2 space-y-0.5">
            <li v-for="(r, i) in dividend.reasons" :key="i" class="text-xs text-gray-600">• {{ r }}</li>
          </ul>
        </div>
      </div>
    </section>

    <!-- Certificates ---------------------------------------------------------- -->
    <section v-else-if="tab === 'certificates'">
      <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
        <select v-model="certFilter" class="rounded border border-gray-300 px-2 py-1 text-sm">
          <option value="issued">Live certificates</option>
          <option value="cancelled">Cancelled</option>
          <option value="">All</option>
        </select>
        <button
          class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
          @click="showIssue = !showIssue"
        >
          {{ showIssue ? "Cancel" : "Issue a certificate" }}
        </button>
      </div>

      <form
        v-if="showIssue"
        class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-5"
        @submit.prevent="issueCertificate"
      >
        <label class="text-xs text-gray-500">
          Member
          <select v-model="issueForm.member_id" required class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option value="">Choose…</option>
            <option v-for="m in heldMembers" :key="m.id" :value="m.id">
              {{ m.member_name }} ({{ m.folio_no }})
            </option>
          </select>
        </label>
        <label class="text-xs text-gray-500">
          Shares
          <input v-model="issueForm.no_of_shares" required type="number" min="1" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Class
          <input v-model="issueForm.share_class" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Face value
          <input v-model="issueForm.face_value" type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <div class="flex items-end">
          <button class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50" :disabled="busy !== ''">
            Issue
          </button>
        </div>
        <p class="col-span-full text-xs text-gray-500">
          Distinctive numbers are allocated by the server from a locked counter — two certificates
          claiming the same numbers would mean two people hold paper for the same shares.
        </p>
      </form>

      <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full divide-y divide-gray-200 text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th class="px-3 py-2">No.</th>
              <th class="px-3 py-2">Holder</th>
              <th class="px-3 py-2 text-right">Shares</th>
              <th class="px-3 py-2">Distinctive</th>
              <th class="px-3 py-2">Type</th>
              <th class="px-3 py-2">Issued</th>
              <th class="px-3 py-2">State</th>
              <th class="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr v-for="c in certificates" :key="c.id" class="hover:bg-gray-50">
              <td class="px-3 py-2 font-mono text-xs">{{ c.certificate_no }}</td>
              <td class="px-3 py-2 font-medium text-gray-900">
                {{ c.holder_name }}
                <span v-if="c.folio_no" class="text-xs text-gray-400">· {{ c.folio_no }}</span>
              </td>
              <td class="px-3 py-2 text-right font-mono">{{ num(c.no_of_shares) }}</td>
              <td class="px-3 py-2 font-mono text-xs text-gray-600">
                {{ c.distinctive_from }}–{{ c.distinctive_to }}
              </td>
              <td class="px-3 py-2 text-xs text-gray-500">{{ c.issue_type }}</td>
              <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">{{ c.issued_on || "deferred" }}</td>
              <td class="px-3 py-2">
                <span class="rounded px-1.5 py-0.5 text-xs" :class="CERT_TONE[c.status]">{{ c.status }}</span>
                <p v-if="c.cancelled_reason" class="mt-0.5 max-w-xs text-xs text-gray-400">
                  {{ c.cancelled_reason }}
                </p>
              </td>
              <td class="px-3 py-2 text-right">
                <button
                  v-if="c.status === 'issued'"
                  class="text-xs text-red-600 hover:underline disabled:opacity-50"
                  :disabled="busy !== ''"
                  @click="cancelCertificate(c)"
                >
                  Cancel &amp; reissue
                </button>
              </td>
            </tr>
            <tr v-if="!certificates.length">
              <td colspan="8" class="px-3 py-8 text-center text-sm text-gray-400">
                No certificates in this view.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Transfers ------------------------------------------------------------- -->
    <section v-else-if="tab === 'transfers'">
      <div class="mb-3 flex justify-end">
        <button
          class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
          @click="showTransfer = !showTransfer"
        >
          {{ showTransfer ? "Cancel" : "Lodge an SH-4" }}
        </button>
      </div>

      <form
        v-if="showTransfer"
        class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-3 lg:grid-cols-6"
        @submit.prevent="lodgeTransfer"
      >
        <label class="text-xs text-gray-500">
          Transferor
          <select v-model="transferForm.transferor_member_id" required class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option value="">Choose…</option>
            <option v-for="m in heldMembers" :key="m.id" :value="m.id">{{ m.member_name }}</option>
          </select>
        </label>
        <label class="text-xs text-gray-500">
          Transferee
          <select v-model="transferForm.transferee_member_id" required class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option value="">Choose…</option>
            <option v-for="m in heldMembers" :key="m.id" :value="m.id">{{ m.member_name }}</option>
          </select>
        </label>
        <label class="text-xs text-gray-500">
          Shares
          <input v-model="transferForm.no_of_shares" required type="number" min="1" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Consideration
          <input v-model="transferForm.consideration" type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Executed on
          <input v-model="transferForm.executed_on" required type="date" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <div class="flex items-end">
          <button class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50" :disabled="busy !== ''">
            Lodge
          </button>
        </div>
      </form>

      <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
        <table class="min-w-full divide-y divide-gray-200 text-sm">
          <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th class="px-3 py-2">Instrument</th>
              <th class="px-3 py-2">From → To</th>
              <th class="px-3 py-2 text-right">Shares</th>
              <th class="px-3 py-2">Distinctive</th>
              <th class="px-3 py-2">Executed</th>
              <th class="px-3 py-2">State</th>
              <th class="px-3 py-2 text-right">Next step</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100">
            <tr v-for="t in transfers" :key="t.id" class="hover:bg-gray-50">
              <td class="px-3 py-2 font-mono text-xs">SH-4/{{ t.instrument_no }}</td>
              <td class="px-3 py-2">
                <span class="font-medium text-gray-900">{{ t.transferor_name }}</span>
                <span class="text-gray-400"> → </span>
                <span class="font-medium text-gray-900">{{ t.transferee_name }}</span>
              </td>
              <td class="px-3 py-2 text-right font-mono">{{ num(t.no_of_shares) }}</td>
              <td class="px-3 py-2 font-mono text-xs text-gray-600">
                {{ t.distinctive_from ? `${t.distinctive_from}–${t.distinctive_to}` : "—" }}
              </td>
              <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">{{ t.executed_on }}</td>
              <td class="px-3 py-2">
                <span class="rounded px-1.5 py-0.5 text-xs" :class="TRANSFER_TONE[t.status]">{{ t.status }}</span>
                <p v-if="t.reverted_reason" class="mt-0.5 max-w-xs text-xs text-red-600">
                  {{ t.reverted_reason }}
                </p>
              </td>
              <td class="whitespace-nowrap px-3 py-2 text-right">
                <button
                  v-if="t.status === 'draft'"
                  class="text-xs text-blue-600 hover:underline disabled:opacity-50"
                  :disabled="busy !== ''"
                  @click="approveTransfer(t)"
                >
                  Board approval
                </button>
                <button
                  v-else-if="t.status === 'board_approved'"
                  class="text-xs text-blue-600 hover:underline disabled:opacity-50"
                  :disabled="busy !== ''"
                  @click="postTransfer(t)"
                >
                  Move the shares
                </button>
                <button
                  v-else-if="t.status === 'issued_posted'"
                  class="text-xs text-red-600 hover:underline disabled:opacity-50"
                  :disabled="busy !== ''"
                  @click="revertTransfer(t)"
                >
                  Revert
                </button>
                <span v-else class="text-xs text-gray-400">—</span>
              </td>
            </tr>
            <tr v-if="!transfers.length">
              <td colspan="7" class="px-3 py-8 text-center text-sm text-gray-400">
                No transfers lodged yet.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Capital events -------------------------------------------------------- -->
    <section v-else>
      <div class="mb-3 flex justify-end">
        <button
          class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
          @click="showEvent = !showEvent"
        >
          {{ showEvent ? "Cancel" : "Open an offer" }}
        </button>
      </div>

      <form
        v-if="showEvent"
        class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-3 lg:grid-cols-6"
        @submit.prevent="createEvent"
      >
        <label class="text-xs text-gray-500">
          Type
          <select v-model="eventForm.event_type" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option v-for="(label, key) in EVENT_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
        </label>
        <label class="text-xs text-gray-500 lg:col-span-2">
          Title
          <input v-model="eventForm.title" required class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Shares offered
          <input v-model="eventForm.shares_offered" type="number" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <label class="text-xs text-gray-500">
          Price / share
          <input v-model="eventForm.price_per_share" type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </label>
        <div class="flex items-end">
          <button class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50" :disabled="busy !== ''">
            Create
          </button>
        </div>
      </form>

      <div class="space-y-3">
        <article
          v-for="e in events"
          :key="e.id"
          class="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
        >
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 class="font-medium text-gray-900">{{ e.title }}</h3>
              <p class="text-xs text-gray-500">
                {{ EVENT_LABELS[e.event_type] }}
                <span v-if="e.shares_offered"> · {{ num(e.shares_offered) }} shares offered</span>
                <span v-if="e.price_per_share"> at {{ num(e.price_per_share) }}</span>
                <span v-if="e.allotted_on"> · allotted {{ e.allotted_on }}</span>
              </p>
            </div>
            <div class="flex items-center gap-2">
              <span class="rounded px-1.5 py-0.5 text-xs" :class="EVENT_TONE[e.status]">{{ e.status }}</span>
              <button
                v-if="e.status === 'draft'"
                class="text-xs text-blue-600 hover:underline disabled:opacity-50"
                :disabled="busy !== ''"
                @click="approveEvent(e)"
              >
                Approve
              </button>
              <button
                v-else-if="e.status === 'approved'"
                class="text-xs text-blue-600 hover:underline disabled:opacity-50"
                :disabled="busy !== ''"
                @click="allotEvent(e)"
              >
                Allot &amp; cut certificates
              </button>
            </div>
          </div>

          <ul v-if="e.allottees?.length" class="mt-2 space-y-0.5 text-xs text-gray-600">
            <li v-for="(a, i) in e.allottees" :key="i">
              • {{ (a as Record<string, string>).name }} — {{ num(String((a as Record<string, string>).shares)) }} shares
            </li>
          </ul>

          <p v-if="e.solvency_check" class="mt-2 rounded bg-gray-50 p-2 text-xs text-gray-600">
            s.123 check at approval: {{ (e.solvency_check as Record<string, string>).verdict }} —
            distributable {{ (e.solvency_check as Record<string, string>).distributable }}
          </p>
        </article>

        <p v-if="!events.length" class="rounded-lg border border-dashed border-gray-300 px-3 py-8 text-center text-sm text-gray-400">
          No capital events yet.
        </p>
      </div>
    </section>
  </div>
</template>
