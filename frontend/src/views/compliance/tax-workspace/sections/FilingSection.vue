<script setup lang="ts">
/**
 * Return Filing — generate the ITR-6 return, record the acknowledgement, and chain
 * a revised, belated or updated return off a filed original.
 *
 * The form code, schema version and payload all come from the statutory field map,
 * so the return is generated rather than assembled by hand.
 */
import { computed, ref } from "vue";
import { api } from "@/api/client";
import InfoTip from "@/components/shared/InfoTip.vue";
import SearchSelect from "@/components/shared/SearchSelect.vue";
import StatusPill from "@/components/shared/StatusPill.vue";
import { useFieldVisibility } from "@/composables/useFieldVisibility";
import type { ErrorEnvelope } from "@/types/core";
import type {
  TaxFiling,
  TaxSectionEmits,
  TaxSectionProps,
  TaxWorkspaceContext,
} from "@/types/taxation";
import { formatDate, toISODateLocal } from "@/utils/format";

const props = defineProps<TaxSectionProps>();
const emit = defineEmits<TaxSectionEmits>();

const context = computed<TaxWorkspaceContext | null>(() => props.workspace.context ?? null);
const { visible } = useFieldVisibility(context);

const generating = ref(false);
const acknowledging = ref(false);
const chaining = ref(false);
const errorText = ref("");
const message = ref("");
const previewOpen = ref(false);

const ackNo = ref("");
const filedOn = ref(toISODateLocal(new Date()));
const verificationMode = ref("EVC");
const chainType = ref("Revised");

const filings = computed<TaxFiling[]>(() => props.workspace.filings ?? []);
const latest = computed<TaxFiling | null>(() => filings.value[0] ?? null);
const blockingCount = computed(() => props.workspace.validation.blocking_count);
const canGenerate = computed(() => blockingCount.value === 0 && !props.disabled);

function errorFrom(e: unknown, fallback: string): string {
  const detail = (e as ErrorEnvelope | null)?.detail;
  return detail ?? (e instanceof Error ? e.message : fallback);
}

function statusTone(status: string): string {
  if (status === "Filed" || status === "Acknowledged") return "complete";
  if (status === "Generated") return "in-progress";
  return "not-started";
}

const payloadPreview = computed<string>(() => {
  const filing = latest.value;
  if (!filing) return "";
  return JSON.stringify(filing.payload, null, 2);
});

async function generateReturn(): Promise<void> {
  generating.value = true;
  errorText.value = "";
  message.value = "";
  try {
    const { data } = await api.post<TaxFiling>("/tax/filings/generate", {
      computation_id: props.workspace.computation.id,
      form_code: context.value?.itr_form_code ?? null,
    });
    message.value = `Generated ${data.form_code} (schema ${data.schema_version}). Review the payload below before uploading it to the portal.`;
    emit("changed");
  } catch (e: unknown) {
    errorText.value = errorFrom(e, "The return could not be generated.");
  } finally {
    generating.value = false;
  }
}

function downloadReturn(): void {
  const filing = latest.value;
  if (!filing) return;
  const blob = new Blob([JSON.stringify(filing.payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${filing.form_code}-${filing.ay_code}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

async function recordAcknowledgement(): Promise<void> {
  const filing = latest.value;
  if (!filing || !ackNo.value.trim()) return;
  acknowledging.value = true;
  errorText.value = "";
  message.value = "";
  try {
    await api.post(`/tax/filings/${filing.id}/acknowledge`, {
      ack_no: ackNo.value.trim(),
      filed_on: filedOn.value || null,
      verification_mode: verificationMode.value || null,
    });
    message.value = "Acknowledgement recorded. Interest for late filing stops accruing on this date.";
    ackNo.value = "";
    emit("changed");
  } catch (e: unknown) {
    errorText.value = errorFrom(e, "The acknowledgement could not be recorded.");
  } finally {
    acknowledging.value = false;
  }
}

async function chainReturn(): Promise<void> {
  chaining.value = true;
  errorText.value = "";
  message.value = "";
  try {
    await api.post("/tax/filings/chain", {
      revises_computation_id: props.workspace.computation.id,
      filing_type: chainType.value,
      ay_code: props.workspace.computation.ay_code,
    });
    message.value = `A ${chainType.value.toLowerCase()} computation has been created for this year. Open it from the Income Tax landing screen.`;
    emit("changed");
  } catch (e: unknown) {
    errorText.value = errorFrom(e, "The follow-on return could not be created.");
  } finally {
    chaining.value = false;
  }
}
</script>

<template>
  <div class="space-y-5" data-testid="tax-section-filing">
    <section class="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-slate-900">
            Return filing
            <InfoTip
              title="Section 139"
              text="The return of income for a company is filed in Form ITR-6. The file generated here is the payload you upload to the income tax portal."
              statutory-ref="Section 139"
              size="md"
            />
          </h2>
          <p class="mt-1 text-sm text-slate-600">
            The return is built from the saved computation through the statutory field map, so the
            figures on it are the figures you reviewed — nothing is retyped.
          </p>
        </div>
        <button
          type="button"
          class="btn-primary"
          :disabled="!canGenerate || generating"
          data-testid="filing-generate"
          @click="generateReturn"
        >
          {{ generating ? "Generating…" : "Generate the return" }}
        </button>
      </div>

      <p
        v-if="blockingCount > 0"
        class="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
        data-testid="filing-blocked"
      >
        {{ blockingCount }} blocking
        {{ blockingCount === 1 ? "issue prevents" : "issues prevent" }} the return being generated.
        <button
          class="font-medium underline"
          type="button"
          data-testid="filing-go-review"
          @click="emit('navigate', 'review')"
        >
          Review and validate
        </button>
        lists them.
      </p>

      <p v-if="message" class="text-sm text-emerald-700" data-testid="filing-message">
        {{ message }}
      </p>
      <p v-if="errorText" class="text-sm text-red-700" data-testid="filing-error">{{ errorText }}</p>
    </section>

    <section
      v-if="latest"
      class="rounded-lg border border-slate-200 bg-white p-4 space-y-3"
      data-testid="filing-latest"
    >
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="text-sm font-semibold text-slate-900">
          {{ latest.form_code }} for {{ latest.ay_code }}
        </h3>
        <StatusPill :status="statusTone(latest.status)" :label="latest.status" size="md" />
      </div>

      <dl class="grid gap-3 text-sm sm:grid-cols-4">
        <div>
          <dt class="text-xs uppercase tracking-wide text-slate-500">Kind of return</dt>
          <dd class="text-slate-800">{{ latest.filing_type }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-slate-500">Schema version</dt>
          <dd class="text-slate-800">{{ latest.schema_version }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-slate-500">Acknowledgement number</dt>
          <dd class="text-slate-800">{{ latest.ack_no ?? "Not filed yet" }}</dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-slate-500">Filed on</dt>
          <dd class="text-slate-800">{{ latest.filed_on ? formatDate(latest.filed_on) : "—" }}</dd>
        </div>
      </dl>

      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="btn-secondary"
          data-testid="filing-download"
          @click="downloadReturn"
        >
          Download the return file
        </button>
        <button
          type="button"
          class="btn-secondary"
          data-testid="filing-toggle-preview"
          @click="previewOpen = !previewOpen"
        >
          {{ previewOpen ? "Hide the return preview" : "Preview the return" }}
        </button>
      </div>

      <pre
        v-if="previewOpen"
        class="max-h-96 overflow-auto rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700"
        data-testid="filing-preview"
        >{{ payloadPreview }}</pre
      >

      <p class="text-xs text-slate-500">
        Payload fingerprint {{ latest.payload_sha256.slice(0, 16) }}… — the file you download is
        exactly what was generated from the saved computation run.
      </p>
    </section>

    <section
      v-if="latest"
      class="rounded-lg border border-slate-200 bg-white p-4 space-y-3"
      data-field="filing.acknowledge"
      tabindex="-1"
    >
      <h3 class="text-sm font-semibold text-slate-900">Record the portal acknowledgement</h3>
      <p class="text-sm text-slate-600">
        After uploading the return to the portal, record the acknowledgement number here. The filing
        date stops interest for late filing accruing.
      </p>
      <div class="grid gap-3 sm:grid-cols-3">
        <div>
          <label class="form-label" for="filing-ack-no">Acknowledgement number</label>
          <input
            id="filing-ack-no"
            v-model="ackNo"
            class="form-input"
            type="text"
            placeholder="As shown on the portal"
            :disabled="acknowledging"
            data-testid="filing-ack-no"
          />
        </div>
        <div>
          <label class="form-label" for="filing-filed-on">Date the return was filed</label>
          <input
            id="filing-filed-on"
            v-model="filedOn"
            class="form-input"
            type="date"
            :disabled="acknowledging"
            data-testid="filing-filed-on"
          />
        </div>
        <div>
          <label class="form-label" for="filing-verification">How it was verified</label>
          <SearchSelect
            v-model="verificationMode"
            :options="props.lookups.verification_modes"
            :disabled="acknowledging"
            placeholder="Choose a verification method"
            testid="filing-verification"
          />
        </div>
      </div>
      <button
        type="button"
        class="btn-primary"
        :disabled="acknowledging || !ackNo.trim()"
        data-testid="filing-acknowledge"
        @click="recordAcknowledgement"
      >
        {{ acknowledging ? "Recording…" : "Record the acknowledgement" }}
      </button>
    </section>

    <section
      v-if="visible('filing.chain')"
      class="rounded-lg border border-slate-200 bg-white p-4 space-y-3"
      data-field="filing.chain"
      tabindex="-1"
    >
      <h3 class="text-sm font-semibold text-slate-900">File a follow-on return</h3>
      <p class="text-sm text-slate-600">
        A revised return corrects a return already filed, a belated return is filed after the due
        date, and an updated return is filed later still under Section 139(8A). Each starts as a
        fresh computation carrying this year's figures forward.
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <div class="w-64">
          <label class="form-label" for="filing-chain-type">Kind of follow-on return</label>
          <SearchSelect
            v-model="chainType"
            :options="props.lookups.filing_types"
            :disabled="chaining"
            placeholder="Choose the kind of return"
            testid="filing-chain-type"
          />
        </div>
        <button
          type="button"
          class="btn-secondary"
          :disabled="chaining"
          data-testid="filing-chain"
          @click="chainReturn"
        >
          {{ chaining ? "Creating…" : "Create the follow-on computation" }}
        </button>
      </div>
    </section>

    <section v-if="filings.length > 1" class="space-y-2" data-testid="filing-history">
      <h3 class="text-sm font-semibold text-slate-900">Earlier returns for this year</h3>
      <div class="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table class="w-full text-sm">
          <thead class="bg-slate-50">
            <tr class="text-left text-xs uppercase tracking-wide text-slate-500">
              <th class="px-3 py-2 font-medium">Return</th>
              <th class="px-3 py-2 font-medium">Kind</th>
              <th class="px-3 py-2 font-medium">Status</th>
              <th class="px-3 py-2 font-medium">Acknowledgement</th>
              <th class="px-3 py-2 font-medium">Filed on</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="filing in filings" :key="filing.id" class="border-t border-slate-100">
              <td class="px-3 py-2 text-slate-800">{{ filing.name }}</td>
              <td class="px-3 py-2 text-slate-600">{{ filing.filing_type }}</td>
              <td class="px-3 py-2">
                <StatusPill :status="statusTone(filing.status)" :label="filing.status" />
              </td>
              <td class="px-3 py-2 text-slate-600">{{ filing.ack_no ?? "—" }}</td>
              <td class="px-3 py-2 text-slate-600">
                {{ filing.filed_on ? formatDate(filing.filed_on) : "—" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
