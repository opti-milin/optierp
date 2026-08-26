<script setup lang="ts">
// "You are working inside a client's own account."
//
// A delegated visit puts the CS inside someone else's tenant, where every record
// belongs to the client and every action is attributed to the firm acting on their
// behalf. That is a state the user must never be in without knowing it — the plan
// calls the entity switcher the safety pattern of this category, and this is the same
// argument one level up: wrong client is bad, wrong *tenant* is worse.
//
// So it is loud, it is always visible while the state holds, and the way out is one
// click and never more than one click away.
import { computed, ref } from "vue";
import { useAuthStore } from "@/stores/auth";
import { useSecretarialStore } from "@/stores/secretarial";

const store = useSecretarialStore();
const auth = useAuthStore();

const leaving = ref(false);
const error = ref("");

// The remembered home is only meaningful while the token is somewhere else. If a hard
// refresh has already dropped us back to the firm, the store clears it rather than
// letting this render a visit that has ended.
const visiting = computed(
  () => store.practiceHome !== null && auth.companyId !== store.practiceHome.companyId,
);

const clientName = computed(() => store.entity?.entity_name ?? "this client");

async function leave(): Promise<void> {
  leaving.value = true;
  error.value = "";
  try {
    await store.leaveDelegated();
  } catch {
    // Most likely the grant was revoked while we were inside it. Saying so is more
    // useful than a generic failure, and the session is still valid for the firm.
    error.value = "Could not switch back. Sign out and in again if this persists.";
  } finally {
    leaving.value = false;
  }
}
</script>

<template>
  <div v-if="visiting" class="border-b border-blue-300 bg-blue-50 px-3 py-2">
    <p class="text-[11px] font-medium uppercase tracking-wide text-blue-700">
      Working inside a client account
    </p>
    <p class="mt-0.5 truncate text-sm font-medium text-blue-900">{{ clientName }}</p>
    <p class="mt-0.5 text-[11px] leading-snug text-blue-700">
      These records belong to the client. Everything you do here is logged against
      {{ store.practiceHome?.name }}.
    </p>
    <button
      class="mt-1.5 w-full rounded border border-blue-400 bg-white px-2 py-1 text-xs font-medium text-blue-800 hover:bg-blue-100 disabled:opacity-50"
      :disabled="leaving"
      @click="leave"
    >
      {{ leaving ? "Switching…" : `← Back to ${store.practiceHome?.name}` }}
    </button>
    <p v-if="error" class="mt-1 text-[11px] text-red-700">{{ error }}</p>
  </div>
</template>
