/** Catalogue accessors for the statutory /tax/catalogue API. */
import { ref } from "vue";
import { api } from "@/api/client";
import type { AssesseeClass, AssessmentYear } from "@/types/taxation";

export function useTaxCatalogue() {
  const assessmentYears = ref<AssessmentYear[]>([]);
  const assesseeClasses = ref<AssesseeClass[]>([]);
  const loading = ref(false);
  const error = ref("");

  async function loadAssessmentYears() {
    loading.value = true;
    error.value = "";
    try {
      assessmentYears.value = (await api.get<AssessmentYear[]>("/tax/catalogue/assessment-years")).data;
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : String(e);
    } finally {
      loading.value = false;
    }
  }

  async function loadAssesseeClasses() {
    assesseeClasses.value = (await api.get<AssesseeClass[]>("/tax/catalogue/assessee-classes")).data;
  }

  async function listItrForms(ayCode: string) {
    return (
      await api.get<Array<{ form_code: string; schema_version: string; title: string; field_maps: unknown[] }>>(
        "/tax/catalogue/itr-forms",
        { params: { ay_code: ayCode } },
      )
    ).data;
  }

  return {
    assessmentYears,
    assesseeClasses,
    loading,
    error,
    loadAssessmentYears,
    loadAssesseeClasses,
    listItrForms,
  };
}
