<script setup lang="ts">
// Create or edit the company/LLP behind a secretarial record.
//
// The word "entity" never appears on screen. Internally an entity and a client
// row are separate things for good reasons (they can live in different accounts,
// and a relationship ends while a company does not) — but that is our problem,
// not the user's. To a CS firm this is a client; to a business it is their company.
import { computed, ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { EntityDetail } from "@/types/secretarial";

const props = withDefaults(
  defineProps<{
    /** Existing record to edit; omit to create. */
    existing?: EntityDetail | null;
    /** Practice mode adds the "do they already use OptiReach?" question. */
    mode?: "client" | "company";
  }>(),
  { existing: null, mode: "company" },
);

const emit = defineEmits<{ saved: [id: string]; cancel: [] }>();

const error = ref<ErrorEnvelope | null>(null);
const saving = ref(false);

// Practice mode only. "Yes" means the client owns their own records, so we must
// NOT create a copy here — they grant access from their side instead. Creating
// one anyway is how you end up with two versions of the same company.
const alreadyOnOptireach = ref(false);

const CLASSES = [
  { value: "private", label: "Private limited" },
  { value: "public", label: "Public limited" },
  { value: "opc", label: "One Person Company" },
  { value: "section8", label: "Section 8 (not-for-profit)" },
  { value: "nidhi", label: "Nidhi" },
  { value: "producer", label: "Producer company" },
];

function blank() {
  return {
    entity_name: "",
    kind: "company" as "company" | "llp",
    entity_class: "private",
    cin: "",
    llpin: "",
    pan: "",
    email: "",
    phone: "",
    incorporated_on: "",
    fy_end_mmdd: "0331",
    is_listed: false,
  };
}

const form = ref(blank());

watch(
  () => props.existing,
  (e) => {
    form.value = e
      ? {
          entity_name: e.entity_name,
          kind: e.kind,
          entity_class: e.entity_class,
          cin: e.cin ?? "",
          llpin: e.llpin ?? "",
          pan: e.pan ?? "",
          email: e.email ?? "",
          phone: e.phone ?? "",
          incorporated_on: e.incorporated_on ?? "",
          fy_end_mmdd: e.fy_end_mmdd,
          is_listed: e.is_listed,
        }
      : blank();
  },
  { immediate: true },
);

const isEdit = computed(() => !!props.existing);
const noun = computed(() => (props.mode === "client" ? "client" : "company"));

async function save(): Promise<void> {
  error.value = null;
  saving.value = true;
  const body = {
    entity_name: form.value.entity_name,
    kind: form.value.kind,
    entity_class: form.value.kind === "llp" ? "llp" : form.value.entity_class,
    cin: form.value.kind === "company" ? form.value.cin || null : null,
    llpin: form.value.kind === "llp" ? form.value.llpin || null : null,
    pan: form.value.pan || null,
    email: form.value.email || null,
    phone: form.value.phone || null,
    incorporated_on: form.value.incorporated_on || null,
    fy_end_mmdd: form.value.fy_end_mmdd,
    is_listed: form.value.is_listed,
  };
  try {
    const resp = props.existing
      ? await api.patch<EntityDetail>(`/secretarial/entities/${props.existing.id}`, body)
      : await api.post<EntityDetail>("/secretarial/entities", body);
    emit("saved", resp.data.id);
  } catch (e) {
    error.value = (e as { response?: { data: ErrorEnvelope } }).response?.data ?? (e as ErrorEnvelope);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div>
    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <!-- Asked in plain words so nobody has to learn what "managed" and
         "delegated" mean; the answer decides where the records actually live. -->
    <div v-if="mode === 'client' && !isEdit" class="mb-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
      <p class="mb-2 text-sm font-medium text-gray-800">Does this client already use OptiReach?</p>
      <label class="mb-1 flex items-start gap-2 text-sm text-gray-700">
        <input v-model="alreadyOnOptireach" type="radio" :value="false" class="mt-1" />
        <span>
          <strong>No</strong> — I'll keep their records here in my account.
          <span class="block text-xs text-gray-500">The usual case.</span>
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm text-gray-700">
        <input v-model="alreadyOnOptireach" type="radio" :value="true" class="mt-1" />
        <span>
          <strong>Yes</strong> — they keep their own records and give me access.
          <span class="block text-xs text-gray-500">
            Their books and registers stay in their account; you work on them from here.
          </span>
        </span>
      </label>
    </div>

    <div
      v-if="alreadyOnOptireach && !isEdit"
      class="rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900"
    >
      <p class="font-medium">Ask them to add you, rather than adding them here.</p>
      <p class="mt-1">
        In their OptiReach account they go to <strong>Secretarial → My CS</strong> and grant you
        access to their company. It then appears in your client list automatically.
      </p>
      <p class="mt-2 text-xs">
        Adding them here as well would create a second copy of the same company, and the two would
        drift apart.
      </p>
      <button class="mt-3 text-xs text-blue-700 underline" @click="emit('cancel')">Close</button>
    </div>

    <template v-else>
      <div class="grid gap-2 sm:grid-cols-3">
        <div class="sm:col-span-2">
          <label class="block text-xs text-gray-500">
            {{ mode === "client" ? "Client name" : "Company name" }}
          </label>
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
          <input
            v-model="form.cin"
            class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
            placeholder="U74999RJ2018PTC060472"
          />
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
          <label class="block text-xs text-gray-500">
            Financial year ends
            <span class="text-gray-400" title="MMDD — 0331 is 31 March, the Indian default">(MMDD)</span>
          </label>
          <input v-model="form.fy_end_mmdd" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500">Email for reminders</label>
          <input v-model="form.email" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label class="block text-xs text-gray-500">Phone</label>
          <input v-model="form.phone" class="w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
        </div>
        <label class="flex items-center gap-1.5 pt-5 text-sm text-gray-600">
          <input v-model="form.is_listed" type="checkbox" /> Listed company
        </label>
      </div>

      <p class="mt-2 text-xs text-gray-500">
        Incorporation date and financial-year end drive every statutory due date, so they are worth
        getting right.
      </p>

      <div class="mt-3 flex gap-2">
        <button
          class="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
          :disabled="!form.entity_name || saving"
          @click="save"
        >
          {{ isEdit ? "Save changes" : `Add ${noun}` }}
        </button>
        <button class="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50" @click="emit('cancel')">
          Cancel
        </button>
      </div>
    </template>
  </div>
</template>
