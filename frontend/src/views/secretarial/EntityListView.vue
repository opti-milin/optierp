<script setup lang="ts">
// Entity list and creation. For a practice this is the client book; for a business
// tenant it is usually one row — their own company, created automatically.
import { ref } from "vue";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope } from "@/types/core";

const store = useSecretarialStore();
const error = ref<ErrorEnvelope | null>(null);
const showAdd = ref(false);
const search = ref("");

const form = ref({
  entity_name: "",
  kind: "company" as "company" | "llp",
  entity_class: "private",
  cin: "",
  llpin: "",
  pan: "",
  incorporated_on: "",
  fy_end_mmdd: "0331",
});

const CLASSES = [
  { value: "private", label: "Private limited" },
  { value: "public", label: "Public limited" },
  { value: "opc", label: "One Person Company" },
  { value: "section8", label: "Section 8 (not-for-profit)" },
  { value: "nidhi", label: "Nidhi" },
  { value: "producer", label: "Producer company" },
];

async function create(): Promise<void> {
  error.value = null;
  try {
    await api.post("/secretarial/entities", {
      entity_name: form.value.entity_name,
      kind: form.value.kind,
      entity_class: form.value.kind === "llp" ? "llp" : form.value.entity_class,
      cin: form.value.kind === "company" ? form.value.cin || null : null,
      llpin: form.value.kind === "llp" ? form.value.llpin || null : null,
      pan: form.value.pan || null,
      incorporated_on: form.value.incorporated_on || null,
      fy_end_mmdd: form.value.fy_end_mmdd,
    });
    showAdd.value = false;
    form.value.entity_name = "";
    form.value.cin = "";
    form.value.llpin = "";
    await store.refreshEntities();
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  }
}

(async () => {
  await store.load();
})();
</script>

<template>
  <div>
    <div class="mb-4 flex items-start justify-between">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Entities</h1>
        <p class="text-sm text-gray-500">Companies and LLPs whose statutory records you keep.</p>
      </div>
      <button
        class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white hover:bg-gray-700"
        @click="showAdd = !showAdd"
      >
        {{ showAdd ? "Cancel" : "Add entity" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div v-if="showAdd" class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div class="grid gap-2 sm:grid-cols-3">
        <div class="sm:col-span-2">
          <label class="block text-xs text-gray-500">Name</label>
          <input v-model="form.entity_name" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500">Type</label>
          <select v-model="form.kind" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option value="company">Company</option>
            <option value="llp">LLP</option>
          </select>
        </div>
        <div v-if="form.kind === 'company'">
          <label class="block text-xs text-gray-500">Class</label>
          <select v-model="form.entity_class" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option v-for="c in CLASSES" :key="c.value" :value="c.value">{{ c.label }}</option>
          </select>
        </div>
        <div v-if="form.kind === 'company'">
          <label class="block text-xs text-gray-500">CIN</label>
          <input v-model="form.cin" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="U74999RJ2018PTC060472" />
        </div>
        <div v-else>
          <label class="block text-xs text-gray-500">LLPIN</label>
          <input v-model="form.llpin" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="AAA-1234" />
        </div>
        <div>
          <label class="block text-xs text-gray-500">PAN</label>
          <input v-model="form.pan" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500">Incorporated on</label>
          <input v-model="form.incorporated_on" type="date" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500">Financial year ends (MMDD)</label>
          <input v-model="form.fy_end_mmdd" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
      </div>
      <button
        class="mt-3 rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
        :disabled="!form.entity_name"
        @click="create"
      >
        Create
      </button>
    </div>

    <input
      v-model="search"
      class="mb-3 w-64 rounded border border-gray-300 px-2 py-1.5 text-sm"
      placeholder="Filter by name…"
    />

    <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">Type</th>
            <th class="px-3 py-2">CIN / LLPIN</th>
            <th class="px-3 py-2">Incorporated</th>
            <th class="px-3 py-2">Status</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="!store.entities.length">
            <td colspan="6" class="px-3 py-6 text-center text-gray-400">No entities yet.</td>
          </tr>
          <tr
            v-for="e in store.entities.filter(
              (x) => !search || x.entity_name.toLowerCase().includes(search.toLowerCase()),
            )"
            :key="e.id"
            class="hover:bg-gray-50"
            :class="e.id === store.entityId ? 'bg-blue-50/50' : ''"
          >
            <td class="px-3 py-2 font-medium text-gray-900">
              {{ e.entity_name }}
              <span
                v-if="e.linked_company_id"
                class="ml-1 rounded bg-green-100 px-1 text-[10px] text-green-800"
                title="This entity's books are kept in this account, so compliance thresholds can be computed from real numbers."
              >
                books here
              </span>
            </td>
            <td class="px-3 py-2 text-gray-600">
              {{ e.kind === "llp" ? "LLP" : e.entity_class }}
              <span v-if="e.is_listed" class="ml-1 text-xs text-gray-400">listed</span>
            </td>
            <td class="px-3 py-2 text-gray-600">{{ e.cin || e.llpin || "—" }}</td>
            <td class="px-3 py-2 text-gray-600">{{ e.incorporated_on || "—" }}</td>
            <td class="px-3 py-2 text-gray-600">{{ e.status }}</td>
            <td class="px-3 py-2 text-right">
              <button
                v-if="e.id !== store.entityId"
                class="text-xs text-blue-600 hover:underline"
                @click="store.setEntity(e.id)"
              >
                Work on this
              </button>
              <span v-else class="text-xs text-gray-400">selected</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
