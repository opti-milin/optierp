<script setup lang="ts">
/** Company tax registration and the per-assessment-year regime election. */
import { onMounted, reactive, ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";

interface TaxRegistration {
  id: string;
  pan: string | null;
  tan: string | null;
  cin: string | null;
  assessee_class_code: string;
  residential_status: string;
  incorporation_date: string | null;
  nature_of_business_codes: string[];
  jurisdiction: string | null;
  default_assessment_year: string | null;
  itr_efile_provider: string | null;
  remarks: string | null;
}

interface TaxRegimeElection {
  id: string;
  ay_code: string;
  regime_code: string;
  assessee_class_code: string;
  elected_on: string | null;
  form_ack_no: string | null;
  irrevocable: boolean;
  remarks: string | null;
}

interface AssesseeClass {
  code: string;
  title: string;
}

interface AssessmentYear {
  code: string;
  fy_start: string;
  fy_end: string;
}

interface TaxRegime {
  code: string;
  title: string;
  assessee_class_code: string;
  election_irrevocable: boolean;
}

const form = reactive({
  pan: "",
  tan: "",
  cin: "",
  assessee_class_code: "Company",
  residential_status: "Resident",
  default_assessment_year: "",
  itr_efile_provider: "",
  jurisdiction: "",
  remarks: "",
});

const election = reactive({
  ay_code: "2025-26",
  regime_code: "Normal",
});

const classes = ref<AssesseeClass[]>([]);
const assessmentYears = ref<AssessmentYear[]>([]);
const regimes = ref<TaxRegime[]>([]);
const elections = ref<TaxRegimeElection[]>([]);
const loading = ref(false);
const saving = ref(false);
const savingElection = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const notice = ref<string | null>(null);

async function loadClasses(): Promise<void> {
  classes.value = (await api.get<AssesseeClass[]>("/tax/catalogue/assessee-classes")).data;
}

async function loadAssessmentYears(): Promise<void> {
  assessmentYears.value = (
    await api.get<AssessmentYear[]>("/tax/catalogue/assessment-years")
  ).data;
}

/** Prior elections are stored by code; show the catalogue's business title instead. */
function regimeLabel(code: string): string {
  return regimes.value.find((r) => r.code === code)?.title ?? code;
}

/** "2025-26 (previous year 2024-25)" — the year a CA thinks in, not a bare code. */
function assessmentYearLabel(year: AssessmentYear): string {
  const previousYear = `${year.fy_start.slice(0, 4)}-${year.fy_end.slice(2, 4)}`;
  return `${year.code} (previous year ${previousYear})`;
}

async function loadRegimes(): Promise<void> {
  regimes.value = (
    await api.get<TaxRegime[]>("/tax/catalogue/regimes", {
      params: { assessee_class_code: form.assessee_class_code },
    })
  ).data;
  if (!regimes.value.some((r) => r.code === election.regime_code) && regimes.value.length) {
    election.regime_code = regimes.value[0]!.code;
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    await loadClasses();
    await loadAssessmentYears();
    const reg = (await api.get<TaxRegistration>("/tax/registrations")).data;
    form.pan = reg.pan ?? "";
    form.tan = reg.tan ?? "";
    form.cin = reg.cin ?? "";
    form.assessee_class_code = reg.assessee_class_code;
    form.residential_status = reg.residential_status;
    form.default_assessment_year = reg.default_assessment_year ?? "";
    form.itr_efile_provider = reg.itr_efile_provider ?? "";
    form.jurisdiction = reg.jurisdiction ?? "";
    form.remarks = reg.remarks ?? "";
    if (reg.default_assessment_year) {
      election.ay_code = reg.default_assessment_year;
    }
    // The catalogue is the only source of valid years; never leave the select on a
    // code the statutory pack does not carry.
    if (
      assessmentYears.value.length &&
      !assessmentYears.value.some((y) => y.code === election.ay_code)
    ) {
      election.ay_code = assessmentYears.value[assessmentYears.value.length - 1]!.code;
    }
    await loadRegimes();
    elections.value = (await api.get<TaxRegimeElection[]>("/tax/elections")).data;
    const current = elections.value.find((e) => e.ay_code === election.ay_code);
    if (current) {
      election.regime_code = current.regime_code;
    }
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    loading.value = false;
  }
}

async function save(): Promise<void> {
  saving.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.put("/tax/registrations", {
      pan: form.pan || null,
      tan: form.tan || null,
      cin: form.cin || null,
      assessee_class_code: form.assessee_class_code,
      residential_status: form.residential_status,
      default_assessment_year: form.default_assessment_year || null,
      itr_efile_provider: form.itr_efile_provider || null,
      jurisdiction: form.jurisdiction || null,
      remarks: form.remarks || null,
      nature_of_business_codes: [],
    });
    notice.value = "Tax registration saved.";
    setTimeout(() => (notice.value = null), 2500);
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    saving.value = false;
  }
}

async function saveElection(): Promise<void> {
  savingElection.value = true;
  error.value = null;
  notice.value = null;
  try {
    await api.post("/tax/elections", {
      ay_code: election.ay_code,
      regime_code: election.regime_code,
      assessee_class_code: form.assessee_class_code,
    });
    notice.value = `Regime election for ${election.ay_code} saved.`;
    setTimeout(() => (notice.value = null), 2500);
    await load();
  } catch (e) {
    error.value = e as ErrorEnvelope;
  } finally {
    savingElection.value = false;
  }
}

watch(
  () => form.assessee_class_code,
  () => {
    void loadRegimes();
  },
);

onMounted(load);
</script>

<template>
  <div class="max-w-2xl space-y-6" data-testid="tax-registration-settings">
    <div>
      <h1 class="text-xl font-semibold text-gray-900">Income Tax Settings</h1>
      <p class="text-sm text-gray-500">
        Tenant tax registration and per-assessment-year regime election. Statutory rates live in the
        <RouterLink to="/tax/catalogue" class="text-blue-600 hover:underline">catalogue</RouterLink>
        (read-only).
      </p>
    </div>

    <p v-if="notice" class="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{{ notice }}</p>
    <p v-if="error" class="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{{ error.detail }}</p>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm space-y-4">
      <h2 class="text-sm font-semibold text-gray-900">Registration</h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">Assessee class</label>
          <select
            v-model="form.assessee_class_code"
            class="form-input"
            data-testid="assessee-class-select"
          >
            <option v-for="c in classes" :key="c.code" :value="c.code">{{ c.title }}</option>
          </select>
        </div>
        <div>
          <label class="form-label">Residential status</label>
          <select v-model="form.residential_status" class="form-input">
            <option value="Resident">Resident</option>
            <option value="NonResident">Non-resident</option>
            <option value="RNOR">Resident but not ordinarily resident</option>
          </select>
        </div>
        <div>
          <label class="form-label">Permanent Account Number</label>
          <input
            v-model="form.pan"
            type="text"
            class="form-input font-mono uppercase"
            maxlength="10"
            placeholder="For example, ABCCE1234F"
            data-testid="tax-pan-input"
          />
          <p class="mt-1 text-xs text-gray-500">
            The fourth character identifies the entity class — C for a company, P for an individual,
            F for a firm, and so on. It must match the class chosen above.
          </p>
        </div>
        <div>
          <label class="form-label">Tax Deduction and Collection Account Number</label>
          <input v-model="form.tan" type="text" class="form-input font-mono uppercase" maxlength="10" />
        </div>
        <div>
          <label class="form-label">Corporate Identity Number</label>
          <input v-model="form.cin" type="text" class="form-input font-mono" />
        </div>
        <div>
          <label class="form-label">Default assessment year</label>
          <select
            v-model="form.default_assessment_year"
            class="form-input"
            data-testid="default-assessment-year"
          >
            <option value="">Not set</option>
            <option v-for="y in assessmentYears" :key="y.code" :value="y.code">
              {{ assessmentYearLabel(y) }}
            </option>
          </select>
        </div>
        <div class="col-span-2">
          <label class="form-label">Jurisdiction</label>
          <input v-model="form.jurisdiction" type="text" class="form-input" />
        </div>
        <div class="col-span-2">
          <label class="form-label">Electronic filing provider</label>
          <select v-model="form.itr_efile_provider" class="form-input">
            <option value="">No provider — generate the return file only</option>
            <option value="sandbox">Sandbox — test acknowledgement, not filed with the portal</option>
          </select>
        </div>
      </div>
      <div class="flex justify-end gap-3">
        <button type="button" class="btn-secondary" :disabled="loading" @click="load">Reload</button>
        <button
          type="button"
          class="btn-primary"
          data-testid="save-registration"
          :disabled="saving || loading"
          @click="save"
        >
          {{ saving ? "Saving…" : "Save registration" }}
        </button>
      </div>
    </section>

    <section class="rounded-lg border border-gray-200 bg-white p-6 shadow-sm space-y-4">
      <h2 class="text-sm font-semibold text-gray-900">
        Tax regime election, for one assessment year
      </h2>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="form-label">Assessment year</label>
          <select v-model="election.ay_code" class="form-input" data-testid="election-ay">
            <option v-for="y in assessmentYears" :key="y.code" :value="y.code">
              {{ assessmentYearLabel(y) }}
            </option>
          </select>
        </div>
        <div>
          <label class="form-label">Regime</label>
          <select v-model="election.regime_code" class="form-input" data-testid="election-regime">
            <option v-for="r in regimes" :key="r.code" :value="r.code">
              {{ r.code }} — {{ r.title }}
            </option>
          </select>
        </div>
      </div>
      <button
        type="button"
        class="btn-primary"
        data-testid="save-election"
        :disabled="savingElection || loading"
        @click="saveElection"
      >
        {{ savingElection ? "Saving…" : "Save election" }}
      </button>

      <div v-if="elections.length" class="pt-2">
        <h3 class="text-xs font-semibold uppercase text-gray-500 mb-2">Prior elections</h3>
        <ul class="text-sm space-y-1" data-testid="election-list">
          <li v-for="e in elections" :key="e.id" class="flex gap-2">
            <span class="font-mono">{{ e.ay_code }}</span>
            <span>{{ regimeLabel(e.regime_code) }}</span>
            <span v-if="e.irrevocable" class="text-amber-600 text-xs">
              cannot be withdrawn once made
            </span>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>
