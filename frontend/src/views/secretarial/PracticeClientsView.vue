<script setup lang="ts">
// The practice client roster: own, managed and delegated clients in one list.
//
// Delegated clients live in another account entirely, so their identity comes off
// the engagement and their counts are filled by the nightly job. Until it has run
// they show zero, which is honest — a made-up number on a compliance dashboard is
// worse than a blank one.
import { computed, ref } from "vue";
import { api } from "@/api/client";
import CompanyForm from "@/components/secretarial/CompanyForm.vue";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { PracticeClient } from "@/types/secretarial";

const store = useSecretarialStore();
const clients = ref<PracticeClient[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const search = ref("");
const stateFilter = ref("");
const showAdd = ref(false);

const RELATIONSHIP_TONE: Record<string, string> = {
  own: "bg-green-100 text-green-800",
  managed: "bg-gray-100 text-gray-700",
  delegated: "bg-blue-100 text-blue-800",
};

const RELATIONSHIP_HINT: Record<string, string> = {
  own: "Your own company — its books are in this account",
  managed: "You hold this client's records",
  delegated: "The client owns these records and has granted you access",
};

const shown = computed(() =>
  clients.value.filter(
    (c) =>
      (!search.value || c.entity_name.toLowerCase().includes(search.value.toLowerCase())) &&
      (!stateFilter.value || c.onboarding_state === stateFilter.value),
  ),
);

const billableCount = computed(() => clients.value.filter((c) => c.billable && c.onboarding_state === "active").length);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    clients.value = (
      await api.get<ListResponse<PracticeClient>>("/secretarial/practice/clients", {
        params: { page_size: 200 },
      })
    ).data.items;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function setState(client: PracticeClient, onboarding_state: string): Promise<void> {
  await api.patch(`/secretarial/practice/clients/${client.id}`, { onboarding_state });
  await load();
}

async function onClientAdded(entityId: string): Promise<void> {
  showAdd.value = false;
  await store.refreshEntities();
  store.setEntity(entityId);
  await load();
}

function openClient(client: PracticeClient): void {
  // Only entities this account owns can be opened directly; a delegated client
  // lives in the client's own tenant and needs a company switch first.
  if (client.relationship_type === "delegated") return;
  store.setEntity(client.entity_id);
}

(async () => {
  await store.load();
  await load();
})();
</script>

<template>
  <div>
    <div class="mb-4 flex items-start justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Clients</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          {{ clients.length }} on the roster · {{ billableCount }} active and billable
        </p>
      </div>
      <button
        class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white hover:bg-gray-700"
        @click="showAdd = !showAdd"
      >
        {{ showAdd ? "Cancel" : "Add client" }}
      </button>
    </div>

    <div v-if="showAdd" class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Add a client</h2>
      <CompanyForm mode="client" @saved="onClientAdded" @cancel="showAdd = false" />
    </div>

    <div class="mb-3 flex flex-wrap items-end gap-2">
      <input
        v-model="search"
        class="w-56 rounded border border-gray-300 px-2 py-1.5 text-sm"
        placeholder="Search clients…"
      />
      <select v-model="stateFilter" class="rounded border border-gray-300 px-2 py-1.5 text-sm">
        <option value="">All states</option>
        <option value="prospect">Prospect</option>
        <option value="onboarding">Onboarding</option>
        <option value="active">Active</option>
        <option value="dormant">Dormant</option>
        <option value="exited">Exited</option>
      </select>
      <button class="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50" @click="load">
        Refresh
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Client</th>
            <th class="px-3 py-2">Relationship</th>
            <th class="px-3 py-2">State</th>
            <th class="px-3 py-2">Next due</th>
            <th class="px-3 py-2 text-right">Open</th>
            <th class="px-3 py-2 text-right">Overdue</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="loading"><td colspan="7" class="px-3 py-6 text-center text-gray-400">Loading…</td></tr>
          <tr v-else-if="!shown.length">
            <td colspan="7" class="px-3 py-6 text-center text-gray-400">
              No clients yet. Press “Add client” above.
            </td>
          </tr>
          <tr v-for="c in shown" :key="c.id" class="hover:bg-gray-50">
            <td class="px-3 py-2">
              <p class="font-medium text-gray-900">{{ c.entity_name }}</p>
              <p class="text-xs text-gray-400">{{ c.registration_no || c.entity_kind }}</p>
            </td>
            <td class="px-3 py-2">
              <span
                class="rounded px-1.5 py-0.5 text-xs"
                :class="RELATIONSHIP_TONE[c.relationship_type]"
                :title="RELATIONSHIP_HINT[c.relationship_type]"
              >
                {{ c.relationship_type }}
              </span>
            </td>
            <td class="px-3 py-2">
              <select
                class="rounded border border-gray-300 px-1.5 py-1 text-xs"
                :value="c.onboarding_state"
                @change="setState(c, ($event.target as HTMLSelectElement).value)"
              >
                <option value="prospect">Prospect</option>
                <option value="onboarding">Onboarding</option>
                <option value="active">Active</option>
                <option value="dormant">Dormant</option>
                <option value="exited">Exited</option>
              </select>
            </td>
            <td class="px-3 py-2 text-gray-600">{{ c.next_due_on ?? "—" }}</td>
            <td class="px-3 py-2 text-right text-gray-600">{{ c.open_item_count }}</td>
            <td class="px-3 py-2 text-right" :class="c.overdue_count ? 'font-medium text-red-600' : 'text-gray-400'">
              {{ c.overdue_count }}
            </td>
            <td class="px-3 py-2 text-right">
              <button
                v-if="c.relationship_type !== 'delegated'"
                class="text-xs text-blue-600 hover:underline"
                @click="openClient(c)"
              >
                Work on this
              </button>
              <span v-else class="text-xs text-gray-400" title="Switch company to open this client's records">
                switch company
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
