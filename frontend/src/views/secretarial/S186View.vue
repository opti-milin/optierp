<script setup lang="ts">
// The s.186 register, and the ceiling it has to stay under.
//
// The headroom bar at the top is the whole reason this screen exists inside an ERP
// rather than in a spreadsheet: the ceiling is computed from the company's own books,
// so it is right without anyone being asked what their free reserves are. Where a
// figure is genuinely missing the verdict is "cannot judge" — never "within limits",
// because being told you are clear on the strength of an absent number is worse than
// being told nothing.
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { Meeting, S186Entry, S186Limit } from "@/types/secretarial";

const store = useSecretarialStore();

const limit = ref<S186Limit | null>(null);
const entries = ref<S186Entry[]>([]);
const meetings = ref<Meeting[]>([]);

const loading = ref(false);
const busy = ref("");
const error = ref<ErrorEnvelope | null>(null);
const statusFilter = ref("outstanding");

// The ceiling is judged against a *year* — s.186(2) reads off that year's balance sheet —
// so the year has to be selectable and visible. Defaulting silently to today's year and
// showing it nowhere is how a register of live entries ends up under a heading that says
// the ceiling cannot be judged, with nothing on screen explaining why.
function indianFy(on: Date): string {
  const year = on.getMonth() >= 3 ? on.getFullYear() : on.getFullYear() - 1;
  return `${year}-${String((year + 1) % 100).padStart(2, "0")}`;
}
const fy = ref(indianFy(new Date()));
const fyOptions = computed(() => {
  const start = Number(fy.value.slice(0, 4));
  return [start + 1, start, start - 1, start - 2].map(
    (y) => `${y}-${String((y + 1) % 100).padStart(2, "0")}`,
  );
});

const adding = ref(false);
const editingLimit = ref(false);

const form = ref({
  entry_type: "loan",
  party_name: "",
  party_relation: "",
  amount: "",
  rate_of_interest: "",
  purpose: "",
  made_on: "",
});

const limitForm = ref({
  free_reserves: "",
  securities_premium: "",
  paid_up_capital: "",
  special_resolution_meeting_id: "",
});

const VERDICT_TONE: Record<string, string> = {
  within: "border-green-300 bg-green-50 text-green-800",
  exceeded: "border-red-300 bg-red-50 text-red-700",
  lifted: "border-blue-300 bg-blue-50 text-blue-800",
  unknown: "border-amber-300 bg-amber-50 text-amber-800",
};

const VERDICT_WORDS: Record<string, string> = {
  within: "Within the s.186(2) ceiling",
  exceeded: "Above the s.186(2) ceiling",
  lifted: "Ceiling lifted by special resolution under s.186(3)",
  unknown: "Cannot be judged — a figure the ceiling depends on is missing",
};

const ENTRY_TONE: Record<string, string> = {
  outstanding: "bg-blue-100 text-blue-800",
  repaid: "bg-green-100 text-green-800",
  invoked: "bg-red-100 text-red-700",
  written_off: "bg-gray-200 text-gray-600",
};

const num = (v: string | null | undefined): string =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });

// How much of the ceiling the register uses, capped at 100 so an overrun still renders.
const usedPct = computed(() => {
  if (!limit.value?.effective_limit || !limit.value.exposure) return 0;
  const pct = (Number(limit.value.exposure) / Number(limit.value.effective_limit)) * 100;
  return Math.min(Math.round(pct), 100);
});

// A general meeting is where a special resolution is passed — the board cannot lift its
// own ceiling, so only AGMs and EGMs are offered here.
const generalMeetings = computed(() => meetings.value.filter((m) => m.meeting_type === "agm" || m.meeting_type === "egm"));

function fail(e: unknown): void {
  error.value = (e as { response?: { data?: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    const [lim, ent, mt] = await Promise.all([
      api.get<S186Limit>("/secretarial/capital/s186/limit", {
        params: { entity_id: store.entityId, fy: fy.value },
      }),
      api.get<ListResponse<S186Entry>>("/secretarial/capital/s186/entries", {
        params: { entity_id: store.entityId, status: statusFilter.value || undefined, page_size: 200 },
      }),
      api.get<ListResponse<Meeting>>("/secretarial/meetings", {
        params: { entity_id: store.entityId, page_size: 200 },
      }),
    ]);
    limit.value = lim.data;
    entries.value = ent.data.items;
    meetings.value = mt.data.items;
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

async function refreshFromLedger(): Promise<void> {
  if (!store.entityId) return;
  busy.value = "refresh";
  error.value = null;
  try {
    limit.value = (
      await api.get<S186Limit>("/secretarial/capital/s186/limit", {
        params: { entity_id: store.entityId, fy: fy.value, refresh: true },
      })
    ).data;
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function saveLimit(): Promise<void> {
  if (!store.entityId || !limit.value) return;
  busy.value = "limit";
  error.value = null;
  try {
    await api.put(
      "/secretarial/capital/s186/limit",
      {
        fy: fy.value,
        free_reserves: limitForm.value.free_reserves || null,
        securities_premium: limitForm.value.securities_premium || null,
        paid_up_capital: limitForm.value.paid_up_capital || null,
        special_resolution_meeting_id: limitForm.value.special_resolution_meeting_id || null,
      },
      { params: { entity_id: store.entityId } },
    );
    editingLimit.value = false;
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function addEntry(): Promise<void> {
  if (!store.entityId) return;
  busy.value = "add";
  error.value = null;
  try {
    await api.post("/secretarial/capital/s186/entries", {
      entity_id: store.entityId,
      entry_type: form.value.entry_type,
      party_name: form.value.party_name,
      party_relation: form.value.party_relation || null,
      amount: form.value.amount,
      rate_of_interest: form.value.rate_of_interest || null,
      purpose: form.value.purpose || null,
      made_on: form.value.made_on,
    });
    adding.value = false;
    form.value = {
      entry_type: "loan",
      party_name: "",
      party_relation: "",
      amount: "",
      rate_of_interest: "",
      purpose: "",
      made_on: "",
    };
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function markRepaid(entry: S186Entry): Promise<void> {
  busy.value = `repay-${entry.id}`;
  error.value = null;
  try {
    await api.patch(`/secretarial/capital/s186/entries/${entry.id}`, {
      repaid_on: new Date().toISOString().slice(0, 10),
      status: "repaid",
    });
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

function openLimitEditor(): void {
  limitForm.value = {
    free_reserves: limit.value?.free_reserves ?? "",
    securities_premium: limit.value?.securities_premium ?? "",
    paid_up_capital: limit.value?.paid_up_capital ?? "",
    special_resolution_meeting_id: limit.value?.special_resolution_meeting_id ?? "",
  };
  editingLimit.value = true;
}

watch(() => [store.entityId, statusFilter.value, fy.value], load);
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Loans &amp; investments (s.186)</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          Every loan, guarantee, security and investment, kept in the register s.186(9) requires —
          and measured against the ceiling s.186(2) sets.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <label class="text-xs text-gray-500">
          Ceiling for FY
          <select v-model="fy" class="ml-1 rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option v-for="option in fyOptions" :key="option" :value="option">{{ option }}</option>
          </select>
        </label>
        <button
          class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
          @click="adding = !adding"
        >
          {{ adding ? "Cancel" : "Add an entry" }}
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <!-- The ceiling ---------------------------------------------------------- -->
    <section
      v-if="limit"
      class="mb-5 rounded-lg border bg-white p-4 shadow-sm"
      :class="VERDICT_TONE[limit.verdict]"
    >
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold">{{ VERDICT_WORDS[limit.verdict] }}</h2>
          <p class="text-xs opacity-80">
            FY {{ limit.fy }} ·
            {{ limit.source === "ledger" ? "computed from this company's ledger" : "from figures entered by hand" }}
            <span v-if="limit.computed_on"> on {{ limit.computed_on }}</span>
          </p>
        </div>
        <div class="flex gap-2">
          <button
            class="rounded border border-current px-2 py-1 text-xs hover:bg-white/50 disabled:opacity-50"
            :disabled="busy !== ''"
            @click="refreshFromLedger"
          >
            Recompute
          </button>
          <button class="rounded border border-current px-2 py-1 text-xs hover:bg-white/50" @click="openLimitEditor">
            Edit figures
          </button>
        </div>
      </div>

      <div class="mt-3 grid gap-4 text-sm sm:grid-cols-4">
        <div>
          <span class="text-xs opacity-70">Paid-up capital</span><br />{{ num(limit.paid_up_capital) }}
        </div>
        <div><span class="text-xs opacity-70">Free reserves</span><br />{{ num(limit.free_reserves) }}</div>
        <div>
          <span class="text-xs opacity-70">Securities premium</span><br />{{ num(limit.securities_premium) }}
        </div>
        <div>
          <span class="text-xs opacity-70">Ceiling (higher of the two tests)</span><br />
          <strong>{{ num(limit.effective_limit) }}</strong>
        </div>
      </div>

      <div v-if="limit.effective_limit" class="mt-3">
        <div class="h-2 w-full overflow-hidden rounded bg-white/60">
          <div
            class="h-full rounded"
            :class="limit.verdict === 'exceeded' ? 'bg-red-500' : 'bg-green-600'"
            :style="{ width: usedPct + '%' }"
          />
        </div>
        <p class="mt-1 text-xs">
          Committed {{ num(limit.exposure) }} of {{ num(limit.effective_limit) }}
          <span v-if="limit.headroom"> · headroom {{ num(limit.headroom) }}</span>
        </p>
        <p class="mt-0.5 text-xs opacity-70">
          Committed is every outstanding entry on the register, whenever it was made —
          s.186(2) caps the aggregate that stands, not one year's additions.
        </p>
      </div>

      <ul v-if="limit.gaps?.length" class="mt-2 space-y-0.5">
        <li v-for="(g, i) in limit.gaps" :key="i" class="text-xs opacity-90">• {{ g }}</li>
      </ul>
      <p v-if="limit.special_resolution_on" class="mt-2 text-xs">
        Special resolution passed on {{ limit.special_resolution_on }}.
      </p>
    </section>

    <!-- Editing the figures -------------------------------------------------- -->
    <form
      v-if="editingLimit"
      class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-5"
      @submit.prevent="saveLimit"
    >
      <label class="text-xs text-gray-500">
        Paid-up capital
        <input v-model="limitForm.paid_up_capital" type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Free reserves
        <input v-model="limitForm.free_reserves" type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Securities premium
        <input v-model="limitForm.securities_premium" type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Special resolution (s.186(3))
        <select v-model="limitForm.special_resolution_meeting_id" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option value="">None</option>
          <option v-for="m in generalMeetings" :key="m.id" :value="m.id">
            {{ m.title || m.meeting_type }} · {{ new Date(m.scheduled_at).toLocaleDateString() }}
          </option>
        </select>
      </label>
      <div class="flex items-end gap-2">
        <button class="flex-1 rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50" :disabled="busy !== ''">
          Save
        </button>
        <button type="button" class="rounded border border-gray-300 px-3 py-2 text-sm" @click="editingLimit = false">
          Cancel
        </button>
      </div>
      <p class="col-span-full text-xs text-gray-500">
        A special resolution is passed by the members, so only general meetings are offered here — a
        board cannot lift its own ceiling.
      </p>
    </form>

    <!-- Adding an entry ------------------------------------------------------ -->
    <form
      v-if="adding"
      class="mb-4 grid gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-3 lg:grid-cols-7"
      @submit.prevent="addEntry"
    >
      <label class="text-xs text-gray-500">
        Type
        <select v-model="form.entry_type" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option value="loan">Loan</option>
          <option value="guarantee">Guarantee</option>
          <option value="security">Security</option>
          <option value="investment">Investment</option>
        </select>
      </label>
      <label class="text-xs text-gray-500 lg:col-span-2">
        Counterparty
        <input v-model="form.party_name" required class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Relation
        <select v-model="form.party_relation" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
          <option value="">Unrelated</option>
          <option value="wholly_owned_subsidiary">Wholly-owned subsidiary</option>
          <option value="subsidiary">Subsidiary</option>
          <option value="joint_venture">Joint venture</option>
          <option value="associate">Associate</option>
        </select>
      </label>
      <label class="text-xs text-gray-500">
        Amount
        <input v-model="form.amount" required type="number" step="0.01" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <label class="text-xs text-gray-500">
        Made on
        <input v-model="form.made_on" required type="date" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <div class="flex items-end">
        <button class="w-full rounded bg-gray-900 px-3 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50" :disabled="busy !== ''">
          Add
        </button>
      </div>
      <label class="col-span-full text-xs text-gray-500">
        Purpose
        <input v-model="form.purpose" class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
      </label>
      <p class="col-span-full text-xs text-gray-500">
        A loan to a wholly-owned subsidiary is exempt from the ceiling under s.186(11), but the
        register entry is still required — so it is recorded and left out of the exposure.
      </p>
    </form>

    <div class="mb-3">
      <select v-model="statusFilter" class="rounded border border-gray-300 px-2 py-1 text-sm">
        <option value="outstanding">Outstanding</option>
        <option value="repaid">Repaid</option>
        <option value="">All</option>
      </select>
    </div>

    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div v-else class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Type</th>
            <th class="px-3 py-2">Counterparty</th>
            <th class="px-3 py-2 text-right">Amount</th>
            <th class="px-3 py-2 text-right">Rate</th>
            <th class="px-3 py-2">Made on</th>
            <th class="px-3 py-2">Purpose</th>
            <th class="px-3 py-2">State</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="e in entries" :key="e.id" class="hover:bg-gray-50">
            <td class="px-3 py-2 text-xs capitalize text-gray-600">{{ e.entry_type }}</td>
            <td class="px-3 py-2 font-medium text-gray-900">
              {{ e.party_name }}
              <span v-if="e.party_relation" class="text-xs text-gray-400">· {{ e.party_relation.replace(/_/g, " ") }}</span>
              <span
                v-if="(e.limit_check as Record<string, string>)?.verdict === 'exempt'"
                class="ml-1 rounded bg-gray-100 px-1 py-0.5 text-xs text-gray-500"
                title="s.186(11) — exempt from the ceiling, still on the register"
              >exempt</span>
            </td>
            <td class="px-3 py-2 text-right font-mono">{{ num(e.amount) }}</td>
            <td class="px-3 py-2 text-right text-xs text-gray-600">{{ e.rate_of_interest ? e.rate_of_interest + "%" : "—" }}</td>
            <td class="whitespace-nowrap px-3 py-2 text-xs text-gray-500">{{ e.made_on }}</td>
            <td class="max-w-xs px-3 py-2 text-xs text-gray-500">{{ e.purpose || "—" }}</td>
            <td class="px-3 py-2">
              <span class="rounded px-1.5 py-0.5 text-xs" :class="ENTRY_TONE[e.status]">{{ e.status.replace(/_/g, " ") }}</span>
              <p v-if="e.repaid_on" class="mt-0.5 text-xs text-gray-400">{{ e.repaid_on }}</p>
            </td>
            <td class="px-3 py-2 text-right">
              <button
                v-if="e.status === 'outstanding'"
                class="text-xs text-blue-600 hover:underline disabled:opacity-50"
                :disabled="busy !== ''"
                @click="markRepaid(e)"
              >
                Mark repaid
              </button>
            </td>
          </tr>
          <tr v-if="!entries.length">
            <td colspan="8" class="px-3 py-8 text-center text-sm text-gray-400">
              Nothing in the register for this filter.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
