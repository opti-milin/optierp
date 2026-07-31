<script setup lang="ts" generic="T extends Record<string, any>">
export interface Column {
  key: string;
  label: string;
  class?: string;
}

defineProps<{
  columns: Column[];
  rows: T[];
  loading?: boolean;
  emptyText?: string;
}>();

const emit = defineEmits<{ rowClick: [row: T] }>();
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
    <table class="min-w-full divide-y divide-gray-200">
      <thead class="bg-gray-50">
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        <tr v-if="loading">
          <td :colspan="columns.length" class="px-4 py-8 text-center text-sm text-gray-400">
            Loading…
          </td>
        </tr>
        <tr v-else-if="rows.length === 0">
          <td :colspan="columns.length" class="px-4 py-8 text-center text-sm text-gray-400">
            {{ emptyText ?? "No records found" }}
          </td>
        </tr>
        <tr
          v-for="(row, i) in rows"
          v-else
          :key="i"
          class="cursor-pointer hover:bg-gray-50"
          @click="emit('rowClick', row)"
        >
          <td v-for="col in columns" :key="col.key" class="px-4 py-3 text-sm text-gray-700" :class="col.class">
            <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
              <!-- Check fields: Yes/No (avoid "—" which looks like a missing value) -->
              <span v-if="typeof row[col.key] === 'boolean'" :class="row[col.key] ? 'text-gray-700' : 'text-gray-400'">
                {{ row[col.key] ? "Yes" : "No" }}
              </span>
              <template v-else>{{ row[col.key] }}</template>
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
