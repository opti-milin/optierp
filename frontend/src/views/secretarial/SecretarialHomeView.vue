<script setup lang="ts">
// The module's front door. Two shells behind one route (plan §2.3):
//  - practice tenants land on the client roster;
//  - business tenants land on their own company's overview.
// Same components either way — only the opening screen differs, because building
// two dashboards would double the maintenance cost of one conditional.
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api } from "@/api/client";
import TrendChart from "@/components/shared/TrendChart.vue";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type {
  ComplianceItem,
  PracticeClient,
  SecretarialWorkspaceStats,
} from "@/types/secretarial";

const store = useSecretarialStore();
const stats = ref<SecretarialWorkspaceStats | null>(null);
const clients = ref<PracticeClient[]>([]);
const upcoming = ref<ComplianceItem[]>([]);
const loading = ref(true);
const error = ref<ErrorEnvelope | null>(null);

const isPractice = computed(() => stats.value?.profile === "practice");

function toneFor(item: ComplianceItem): string {
  if (item.status === "overdue") return "bg-red-100 text-red-800";
  if ((item.days_to_due ?? 99) <= 14) return "bg-amber-100 text-amber-800";
  return "bg-gray-100 text-gray-700";
}

function dueLabel(item: ComplianceItem): string {
  const d = item.days_to_due ?? 0;
  if (d < 0) return `${Math.abs(d)}d overdue`;
  if (d === 0) return "due today";
  return `in ${d}d`;
}

onMounted(async () => {
  try {
    await store.load();
    stats.value = (await api.get<SecretarialWorkspaceStats>("/secretarial/workspace")).data;

    if (stats.value.profile === "practice") {
      clients.value = (
        await api.get<ListResponse<PracticeClient>>("/secretarial/practice/clients", {
          params: { page_size: 12 },
        })
      ).data.items;
    }

    upcoming.value = (
      await api.get<ListResponse<ComplianceItem>>("/secretarial/compliance/items", {
        params: { open_only: true, page_size: 8 },
      })
    ).data.items;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div>
    <div class="mb-5">
      <h1 class="text-xl font-semibold text-gray-900">
        {{ isPractice ? store.settings?.practice_name || "Secretarial practice" : "Secretarial & Compliance" }}
      </h1>
      <p class="max-w-3xl text-sm text-gray-500">
        {{
          isPractice
            ? "Your clients, their statutory registers, and what each of them owes the Registrar."
            : "Directors and members, statutory registers, and the ROC filings this company owes."
        }}
      </p>
    </div>

    <p v-if="error" class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <template v-if="stats">
      <div class="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <div
          v-for="card in stats.cards"
          :key="card.label"
          class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm"
        >
          <p class="text-[11px] uppercase tracking-wide text-gray-400">{{ card.label }}</p>
          <p
            class="mt-1 text-2xl font-semibold"
            :class="
              card.label === 'Overdue' && card.value > 0
                ? 'text-red-600'
                : card.label.startsWith('Rules awaiting')
                  ? 'text-amber-600'
                  : 'text-gray-900'
            "
          >
            {{ card.value }}
          </p>
        </div>
      </div>

      <!-- The review backlog is surfaced here on purpose: a working engine running on
           unreviewed statutory text is not a finished module. -->
      <div
        v-if="stats.cards.some((c) => c.label.startsWith('Rules awaiting'))"
        class="mb-6 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
      >
        Some compliance rules have not been reviewed by a qualified professional yet. They are
        excluded from every calendar until they are published —
        <RouterLink to="/secretarial/rules" class="font-medium underline">review them</RouterLink>.
      </div>

      <div class="grid gap-5 lg:grid-cols-3">
        <div class="lg:col-span-2">
          <div class="mb-2 flex items-baseline justify-between">
            <h2 class="text-sm font-semibold text-gray-900">
              {{ isPractice ? "Clients" : "Registers" }}
            </h2>
            <RouterLink
              :to="isPractice ? '/secretarial/clients' : '/secretarial/company'"
              class="text-xs text-blue-600 hover:underline"
            >
              {{ isPractice ? "View all →" : "Company details →" }}
            </RouterLink>
          </div>

          <div
            v-if="isPractice"
            class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
          >
            <table class="min-w-full divide-y divide-gray-200 text-sm">
              <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th class="px-3 py-2">Client</th>
                  <th class="px-3 py-2">Relationship</th>
                  <th class="px-3 py-2">Next due</th>
                  <th class="px-3 py-2 text-right">Open</th>
                  <th class="px-3 py-2 text-right">Overdue</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100">
                <tr v-if="!clients.length">
                  <td colspan="5" class="px-3 py-6 text-center text-gray-400">
                    No clients yet. Use “Add client” to get started.
                  </td>
                </tr>
                <tr v-for="c in clients" :key="c.id" class="hover:bg-gray-50">
                  <td class="px-3 py-2 font-medium text-gray-900">{{ c.entity_name }}</td>
                  <td class="px-3 py-2">
                    <span
                      class="rounded px-1.5 py-0.5 text-xs"
                      :class="
                        c.relationship_type === 'delegated'
                          ? 'bg-blue-100 text-blue-800'
                          : c.relationship_type === 'own'
                            ? 'bg-green-100 text-green-800'
                            : 'bg-gray-100 text-gray-700'
                      "
                    >
                      {{ c.relationship_type }}
                    </span>
                  </td>
                  <td class="px-3 py-2 text-gray-600">{{ c.next_due_on ?? "—" }}</td>
                  <td class="px-3 py-2 text-right text-gray-600">{{ c.open_item_count }}</td>
                  <td
                    class="px-3 py-2 text-right"
                    :class="c.overdue_count ? 'font-medium text-red-600' : 'text-gray-400'"
                  >
                    {{ c.overdue_count }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-else class="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <RouterLink
              v-for="link in [
                { to: '/secretarial/registers/members', label: 'Members', hint: 's.88' },
                { to: '/secretarial/directors', label: 'Directors & KMP', hint: 's.170' },
                { to: '/secretarial/registers/committees', label: 'Committees', hint: 'Board' },
                { to: '/secretarial/registers/charges', label: 'Charges', hint: 's.85' },
                { to: '/secretarial/registers/auditors', label: 'Auditors', hint: 's.139' },
                {
                  to: '/secretarial/registers/beneficial-owners',
                  label: 'Beneficial owners',
                  hint: 's.90',
                },
              ]"
              :key="link.to"
              :to="link.to"
              class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm hover:border-blue-300"
            >
              <p class="text-sm font-medium text-gray-900">{{ link.label }}</p>
              <p class="text-xs text-gray-400">{{ link.hint }}</p>
            </RouterLink>
          </div>

          <div class="mt-5">
            <h2 class="mb-2 text-sm font-semibold text-gray-900">{{ stats.chart_title }}</h2>
            <div class="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
              <TrendChart :points="stats.trend" value-format="int" :height="200" />
            </div>
          </div>
        </div>

        <div>
          <div class="mb-2 flex items-baseline justify-between">
            <h2 class="text-sm font-semibold text-gray-900">What's due next</h2>
            <RouterLink to="/secretarial/compliance" class="text-xs text-blue-600 hover:underline">
              Calendar →
            </RouterLink>
          </div>
          <div class="space-y-2">
            <p v-if="!upcoming.length" class="rounded border border-gray-200 bg-white p-3 text-sm text-gray-400">
              Nothing outstanding. Generate the calendar for a financial year to populate this.
            </p>
            <div
              v-for="item in upcoming"
              :key="item.id"
              class="rounded-lg border border-gray-200 bg-white p-2.5 shadow-sm"
            >
              <div class="flex items-start justify-between gap-2">
                <p class="text-sm font-medium leading-snug text-gray-900">{{ item.title }}</p>
                <span class="shrink-0 rounded px-1.5 py-0.5 text-[11px]" :class="toneFor(item)">
                  {{ dueLabel(item) }}
                </span>
              </div>
              <p class="mt-0.5 text-xs text-gray-400">
                {{ item.entity_name }} · {{ item.form_code || item.act_section || item.fy }}
              </p>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
