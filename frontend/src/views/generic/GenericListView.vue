<script setup lang="ts">
// Generic list view — renders the list page for ANY registered DocType from
// its /meta config. No per-doctype code (the metadata engine, "the machine").

import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "@/api/client";
import DataTable, { type Column } from "@/components/shared/DataTable.vue";
import ImportFromTallyButton from "@/components/shared/ImportFromTallyButton.vue";
import TreeView from "@/components/shared/TreeView.vue";
import { useList } from "@/composables/useList";
import type { DocTypeMeta } from "@/types/registry";
import { SLUG_TO_TALLY_ENTITY } from "@/types/tally";

const route = useRoute();
const router = useRouter();

const doctype = ref<string>(route.params.doctype as string);
const meta = ref<DocTypeMeta | null>(null);
const columns = ref<Column[]>([]);

const { items, total, page, pageSize, loading, error, goToPage, reset } = useList<
  Record<string, unknown> & { id: string }
>(() => `/registry/${doctype.value}`);

async function init(): Promise<void> {
  doctype.value = route.params.doctype as string;
  meta.value = (await api.get<DocTypeMeta>(`/meta/${doctype.value}`)).data;
  columns.value = meta.value.list_fields.map((c) => ({ key: c.key, label: c.label }));
  if (!meta.value.is_tree) await reset();
}

// Masters that a Tally export can fill get an "Import from Tally" button here,
// so every new engine-backed master inherits the option for free.
const tallySource = computed(() => SLUG_TO_TALLY_ENTITY[doctype.value]);

onMounted(init);
watch(() => route.params.doctype, init);

function openNew(): void {
  void router.push(`/m/${doctype.value}/new`);
}
function openRow(row: { id: string }): void {
  void router.push(`/m/${doctype.value}/${row.id}`);
}
</script>

<template>
  <div>
    <div class="mb-4 flex items-center justify-between">
      <h1 class="text-xl font-semibold text-gray-900">{{ meta?.name ?? "Records" }}</h1>
      <div class="flex items-center gap-2">
        <ImportFromTallyButton
          v-if="tallySource"
          :module="tallySource.module"
          :entity="tallySource.entity"
        />
        <button
          class="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
          @click="openNew"
        >
          + New
        </button>
      </div>
    </div>

    <p v-if="error" class="mb-3 text-sm text-red-600">{{ error.detail }}</p>

    <TreeView v-if="meta?.is_tree" :doctype="doctype" :title-field="meta.title_field" />

    <template v-else>
      <DataTable :columns="columns" :rows="items" :loading="loading" @row-click="openRow" />

      <div class="mt-3 flex items-center justify-between text-sm text-gray-500">
        <span>{{ total }} record(s)</span>
        <div class="flex items-center gap-2">
          <button
            class="rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
            :disabled="page <= 1"
            @click="goToPage(page - 1)"
          >
            Prev
          </button>
          <span>Page {{ page }}</span>
          <button
            class="rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
            :disabled="page * pageSize >= total"
            @click="goToPage(page + 1)"
          >
            Next
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
