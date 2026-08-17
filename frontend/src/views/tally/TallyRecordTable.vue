<script setup lang="ts">
// Per-record view of an import. This is the screen a tester lives on when
// comparing against Tally: filter to Error, read why each one failed, fix the
// mapping, roll back, re-run.
import { computed, onMounted, ref, watch } from "vue";
import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import type { ErrorEnvelope } from "@/types/core";
import type { TallyImportEntity, TallyStagingList, TallyStagingRecord } from "@/types/tally";

const props = defineProps<{ importId: string; entities: TallyImportEntity[] }>();

const rows = ref<TallyStagingRecord[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = 100;
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);

const fEntity = ref("");
const fStatus = ref("");
const fSearch = ref("");
const expanded = ref<string | null>(null);

const STATUS_TONE: Record<string, string> = {
  Imported: "bg-green-50 text-green-700",
  Warning: "bg-amber-50 text-amber-700",
  Error: "bg-red-50 text-red-700",
  Skipped: "bg-gray-100 text-gray-600",
  Pending: "bg-blue-50 text-blue-700",
  "Rolled Back": "bg-gray-200 text-gray-700",
};

const pageLabel = computed(() => {
  if (!total.value) return "0 records";
  return `${offset.value + 1}–${Math.min(offset.value + limit, total.value)} of ${total.value}`;
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const data = (
      await api.get<TallyStagingList>(`/tally/imports/${props.importId}/records`, {
        params: {
          entity_key: fEntity.value || undefined,
          status: fStatus.value || undefined,
          search: fSearch.value || undefined,
          limit,
          offset: offset.value,
        },
      })
    ).data;
    rows.value = data.items;
    total.value = data.total;
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

function page(delta: number): void {
  offset.value = Math.max(0, offset.value + delta * limit);
  void load();
}

watch([fEntity, fStatus], () => {
  offset.value = 0;
  void load();
});
onMounted(load);
</script>

<template>
  <div>
    <div class="mb-3 flex flex-wrap items-end gap-3">
      <div>
        <label class="form-label">Entity</label>
        <select v-model="fEntity" class="form-input">
          <option value="">All</option>
          <option v-for="entity in props.entities" :key="entity.id" :value="entity.entity_key">
            {{ entity.label }} ({{ entity.total }})
          </option>
        </select>
      </div>
      <div>
        <label class="form-label">Status</label>
        <select v-model="fStatus" class="form-input">
          <option value="">All</option>
          <option value="Imported">Imported</option>
          <option value="Warning">Imported with a note</option>
          <option value="Error">Failed</option>
          <option value="Skipped">Skipped</option>
          <option value="Pending">Not run yet</option>
          <option value="Rolled Back">Rolled back</option>
        </select>
      </div>
      <div>
        <label class="form-label">Search</label>
        <input
          v-model="fSearch"
          type="search"
          class="form-input"
          placeholder="name or voucher no."
          @keyup.enter="load"
        />
      </div>
      <button class="btn-secondary" @click="load">Refresh</button>
      <div class="ml-auto flex items-center gap-2 text-sm text-gray-500">
        <span>{{ pageLabel }}</span>
        <button class="btn-secondary" :disabled="offset === 0" @click="page(-1)">Prev</button>
        <button class="btn-secondary" :disabled="offset + limit >= total" @click="page(1)">
          Next
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div class="rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full text-sm">
        <thead class="border-b border-gray-200 text-left text-xs uppercase text-gray-400">
          <tr>
            <th class="px-4 py-2">Tally record</th>
            <th class="px-4 py-2">Voucher / date</th>
            <th class="px-4 py-2">Became</th>
            <th class="px-4 py-2">Status</th>
            <th class="px-4 py-2">Notes</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="5" class="px-4 py-6 text-center text-gray-400">Loading…</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td colspan="5" class="px-4 py-6 text-center text-gray-400">
              Nothing matches these filters.
            </td>
          </tr>
          <template v-for="row in rows" :key="row.id">
            <tr
              class="cursor-pointer border-b border-gray-100 hover:bg-gray-50"
              @click="expanded = expanded === row.id ? null : row.id"
            >
              <td class="px-4 py-2">
                <div class="font-medium text-gray-900">{{ row.tally_name || "—" }}</div>
                <div class="text-xs text-gray-400">
                  {{ row.entity_key }}<span v-if="row.tally_parent"> · under {{ row.tally_parent }}</span>
                </div>
              </td>
              <td class="px-4 py-2 text-gray-700">
                <span v-if="row.voucher_number">{{ row.voucher_number }}</span>
                <span v-if="row.posting_date" class="block text-xs text-gray-400">
                  {{ formatDate(row.posting_date) }}
                </span>
                <span v-if="!row.voucher_number && !row.posting_date">—</span>
              </td>
              <td class="px-4 py-2 text-gray-700">
                <span v-if="row.target_name">{{ row.target_doctype }} {{ row.target_name }}</span>
                <span v-else class="text-gray-400">—</span>
              </td>
              <td class="px-4 py-2">
                <span
                  class="rounded px-2 py-0.5 text-xs font-medium"
                  :class="STATUS_TONE[row.status] ?? 'bg-gray-100 text-gray-600'"
                >
                  {{ row.status }}
                </span>
              </td>
              <td class="px-4 py-2 text-gray-600">
                <span v-if="row.messages?.length">
                  {{ row.messages[0].message }}
                  <span v-if="row.messages.length > 1" class="text-xs text-gray-400">
                    +{{ row.messages.length - 1 }} more
                  </span>
                </span>
                <span v-else class="text-gray-300">—</span>
              </td>
            </tr>
            <tr v-if="expanded === row.id && row.messages?.length" class="border-b border-gray-100 bg-gray-50">
              <td colspan="5" class="px-4 py-3">
                <ul class="space-y-1 text-sm">
                  <li v-for="(message, idx) in row.messages" :key="idx" class="flex gap-2">
                    <span
                      class="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                      :class="{
                        'bg-red-500': message.level === 'error',
                        'bg-amber-500': message.level === 'warning',
                        'bg-gray-400': message.level === 'info',
                      }"
                    />
                    <span class="text-gray-700">
                      {{ message.message }}
                      <em v-if="message.field" class="text-gray-400">({{ message.field }})</em>
                    </span>
                  </li>
                </ul>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>
