<script setup lang="ts">
// Register of directors, partners and KMP for the working entity, plus the
// add/cease actions. Ceasing is a dated close, never a delete — past minutes
// refer to these rows.
import { ref } from "vue";
import { api } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { Appointment, Person } from "@/types/secretarial";

const store = useSecretarialStore();
const rows = ref<Appointment[]>([]);
const people = ref<Person[]>([]);
const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const showAdd = ref(false);
const includeCeased = ref(true);

const form = ref({
  person_id: "",
  role_type: "director",
  designation: "",
  appointed_on: new Date().toISOString().slice(0, 10),
  is_signing: false,
  is_chairperson: false,
});

const newPerson = ref({ full_name: "", din: "", pan: "", email: "" });

const ROLES_BY_KIND: Record<string, string[]> = {
  company: ["director", "kmp", "auditor", "secretary"],
  llp: ["designated_partner", "partner", "auditor"],
};

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    rows.value = (
      await api.get<ListResponse<Appointment>>(
        `/secretarial/entities/${store.entityId}/appointments`,
        { params: { include_ceased: includeCeased.value, page_size: 200 } },
      )
    ).data.items;
    people.value = (
      await api.get<ListResponse<Person>>("/secretarial/persons", { params: { page_size: 200 } })
    ).data.items;
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    loading.value = false;
  }
}

async function createPerson(): Promise<void> {
  error.value = null;
  try {
    const person = (
      await api.post<Person>("/secretarial/persons", {
        full_name: newPerson.value.full_name,
        din: newPerson.value.din || null,
        pan: newPerson.value.pan || null,
        email: newPerson.value.email || null,
      })
    ).data;
    people.value.push(person);
    form.value.person_id = person.id;
    newPerson.value = { full_name: "", din: "", pan: "", email: "" };
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  }
}

async function appoint(): Promise<void> {
  error.value = null;
  try {
    await api.post("/secretarial/appointments", {
      entity_id: store.entityId,
      person_id: form.value.person_id,
      role_type: form.value.role_type,
      designation: form.value.designation || null,
      appointed_on: form.value.appointed_on,
      is_signing: form.value.is_signing,
      is_chairperson: form.value.is_chairperson,
    });
    showAdd.value = false;
    await load();
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  }
}

async function cease(row: Appointment): Promise<void> {
  const reason = window.prompt(`Why is ${row.person_name} ceasing office?`);
  if (!reason) return;
  const on = window.prompt("Effective date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10));
  if (!on) return;
  error.value = null;
  try {
    await api.post(`/secretarial/appointments/${row.id}/cease`, {
      ceased_on: on,
      cessation_reason: reason,
    });
    await load();
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  }
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
        <h1 class="text-xl font-semibold text-gray-900">Directors, partners & KMP</h1>
        <p class="text-sm text-gray-500">
          {{ store.entity?.entity_name ?? "No entity selected" }} · s.170 register
        </p>
      </div>
      <button
        class="rounded bg-gray-800 px-3 py-1.5 text-sm text-white hover:bg-gray-700"
        @click="showAdd = !showAdd"
      >
        {{ showAdd ? "Cancel" : "Appoint" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <div v-if="showAdd" class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">Appoint to office</h2>

      <div class="mb-3 grid gap-2 sm:grid-cols-4">
        <div class="sm:col-span-2">
          <label class="block text-xs text-gray-500">Person</label>
          <select v-model="form.person_id" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option value="">Select…</option>
            <option v-for="p in people" :key="p.id" :value="p.id">
              {{ p.full_name }}{{ p.din ? ` (DIN ${p.din})` : "" }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Role</label>
          <select v-model="form.role_type" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm">
            <option v-for="r in ROLES_BY_KIND[store.entity?.kind ?? 'company']" :key="r" :value="r">
              {{ r.replace("_", " ") }}
            </option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-gray-500">Appointed on</label>
          <input v-model="form.appointed_on" type="date" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <div class="sm:col-span-2">
          <label class="block text-xs text-gray-500">Designation</label>
          <input
            v-model="form.designation"
            class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
            placeholder="Managing Director, CFO, Independent Director…"
          />
        </div>
        <label class="flex items-center gap-1.5 pt-5 text-sm text-gray-600">
          <input v-model="form.is_signing" type="checkbox" /> Signs board papers
        </label>
        <label class="flex items-center gap-1.5 pt-5 text-sm text-gray-600">
          <input v-model="form.is_chairperson" type="checkbox" /> Chairperson
        </label>
      </div>

      <details class="mb-3 rounded border border-gray-200 bg-gray-50 p-3">
        <summary class="cursor-pointer text-xs font-medium text-gray-600">
          Person not on the list? Add them
        </summary>
        <p class="mb-2 mt-2 text-xs text-gray-500">
          People are shared across every entity in this account, so a director sitting on three
          boards is one record — that is what makes batch declarations possible.
        </p>
        <div class="grid gap-2 sm:grid-cols-4">
          <input v-model="newPerson.full_name" class="rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="Full name" />
          <input v-model="newPerson.din" class="rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="DIN / DPIN" />
          <input v-model="newPerson.pan" class="rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="PAN" />
          <input v-model="newPerson.email" class="rounded border border-gray-300 px-2 py-1.5 text-sm" placeholder="Email" />
        </div>
        <button
          class="mt-2 rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-white"
          :disabled="!newPerson.full_name"
          @click="createPerson"
        >
          Add person
        </button>
      </details>

      <button
        class="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
        :disabled="!form.person_id"
        @click="appoint"
      >
        Appoint
      </button>
    </div>

    <label class="mb-2 flex items-center gap-1.5 text-sm text-gray-600">
      <input v-model="includeCeased" type="checkbox" @change="load" /> Show past officers
    </label>

    <div class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">Role</th>
            <th class="px-3 py-2">Designation</th>
            <th class="px-3 py-2">Appointed</th>
            <th class="px-3 py-2">Ceased</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <tr v-if="loading"><td colspan="6" class="px-3 py-6 text-center text-gray-400">Loading…</td></tr>
          <tr v-else-if="!rows.length">
            <td colspan="6" class="px-3 py-6 text-center text-gray-400">No officers recorded yet.</td>
          </tr>
          <tr v-for="row in rows" :key="row.id" class="hover:bg-gray-50" :class="row.ceased_on ? 'text-gray-400' : ''">
            <td class="px-3 py-2 font-medium" :class="row.ceased_on ? '' : 'text-gray-900'">
              {{ row.person_name }}
              <span v-if="row.is_chairperson" class="ml-1 rounded bg-purple-100 px-1 text-[10px] text-purple-800">chair</span>
              <span v-if="row.is_signing" class="ml-1 rounded bg-blue-100 px-1 text-[10px] text-blue-800">signs</span>
            </td>
            <td class="px-3 py-2">{{ row.role_type.replace("_", " ") }}</td>
            <td class="px-3 py-2">{{ row.designation || "—" }}</td>
            <td class="px-3 py-2">{{ row.appointed_on }}</td>
            <td class="px-3 py-2">
              {{ row.ceased_on || "—" }}
              <span v-if="row.cessation_reason" class="block text-xs">{{ row.cessation_reason }}</span>
            </td>
            <td class="px-3 py-2 text-right">
              <button
                v-if="!row.ceased_on"
                class="text-xs text-red-600 hover:underline"
                @click="cease(row)"
              >
                Cease
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
