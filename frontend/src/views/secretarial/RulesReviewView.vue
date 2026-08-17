<script setup lang="ts">
// The legal-content review queue.
//
// Engineering builds the engine; a qualified Company Secretary approves the wording
// and the applicability. Until a rule is published it cannot generate a single
// calendar row — which is exactly why the whole module could be built before any of
// this text was signed off.
import { computed, ref } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { ComplianceRule } from "@/types/secretarial";

const rules = ref<ComplianceRule[]>([]);
const counts = ref<Record<string, number>>({});
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const filter = ref("");

const NEXT: Record<string, string> = {
  draft: "reviewed",
  reviewed: "approved",
  approved: "published",
};

const TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  reviewed: "bg-blue-100 text-blue-800",
  approved: "bg-purple-100 text-purple-800",
  published: "bg-green-100 text-green-800",
  retired: "bg-gray-200 text-gray-500",
};

const shown = computed(() =>
  filter.value ? rules.value.filter((r) => r.review_status === filter.value) : rules.value,
);

const publishedCount = computed(() => counts.value.published ?? 0);
const totalCount = computed(() => Object.values(counts.value).reduce((a, b) => a + b, 0));

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    rules.value = (await api.get<ComplianceRule[]>("/secretarial/compliance/rules")).data;
    counts.value = (await api.get<Record<string, number>>("/secretarial/compliance/rules/review-status")).data;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function advance(rule: ComplianceRule): Promise<void> {
  const target = NEXT[rule.review_status];
  if (!target) return;

  const body: Record<string, unknown> = { review_status: target };
  if (target === "published") {
    const name = window.prompt(
      "Publishing makes this rule generate real compliance deadlines.\n\n" +
        "Name of the Company Secretary or legal professional who reviewed this wording:",
      rule.reviewer_name ?? "",
    );
    if (!name) return;
    const credential = window.prompt("Membership number (e.g. ACS 12345), optional:", "");
    body.reviewer_name = name;
    body.reviewer_credential = credential || null;
    body.reviewed_on = new Date().toISOString().slice(0, 10);
  }

  error.value = null;
  try {
    await api.post(`/secretarial/compliance/rules/${rule.id}/review`, body);
    await load();
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  }
}

load();
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-xl font-semibold text-gray-900">Statutory content review</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        Every compliance rule carries its source citation and moves through
        draft → reviewed → approved → published. Only published rules generate deadlines, so
        unreviewed wording can never reach a client's calendar.
      </p>
    </div>

    <div class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div class="flex flex-wrap items-center gap-4">
        <div>
          <p class="text-[11px] uppercase tracking-wide text-gray-400">Published</p>
          <p class="text-2xl font-semibold" :class="publishedCount ? 'text-green-600' : 'text-gray-400'">
            {{ publishedCount }} / {{ totalCount }}
          </p>
        </div>
        <div class="flex flex-wrap gap-1.5">
          <button
            class="rounded px-2 py-1 text-xs"
            :class="filter === '' ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600'"
            @click="filter = ''"
          >
            All
          </button>
          <button
            v-for="(n, status) in counts"
            :key="status"
            class="rounded px-2 py-1 text-xs"
            :class="filter === status ? 'bg-gray-800 text-white' : TONE[status]"
            @click="filter = status"
          >
            {{ status }} ({{ n }})
          </button>
        </div>
      </div>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>
    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Code</th>
            <th class="px-3 py-2">Obligation</th>
            <th class="px-3 py-2">Source</th>
            <th class="px-3 py-2">Reviewed by</th>
            <th class="px-3 py-2">State</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-for="rule in shown" :key="rule.id" class="hover:bg-gray-50">
            <td class="whitespace-nowrap px-3 py-2 font-mono text-xs text-gray-600">{{ rule.code }}</td>
            <td class="px-3 py-2">
              <p class="font-medium text-gray-900">{{ rule.title }}</p>
              <p class="text-xs text-gray-400">{{ rule.act }} {{ rule.section }}</p>
            </td>
            <td class="px-3 py-2 text-xs text-gray-500">{{ rule.source_ref || "—" }}</td>
            <td class="px-3 py-2 text-xs text-gray-500">
              {{ rule.reviewer_name || "—" }}
              <span v-if="rule.reviewed_on" class="block">{{ rule.reviewed_on }}</span>
            </td>
            <td class="px-3 py-2">
              <span class="rounded px-1.5 py-0.5 text-xs" :class="TONE[rule.review_status]">
                {{ rule.review_status }}
              </span>
            </td>
            <td class="px-3 py-2 text-right">
              <button
                v-if="NEXT[rule.review_status]"
                class="text-xs text-blue-600 hover:underline"
                @click="advance(rule)"
              >
                Mark {{ NEXT[rule.review_status] }} →
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
