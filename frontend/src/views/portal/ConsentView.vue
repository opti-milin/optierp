<script setup lang="ts">
// /p/r/:token — a director records consent on a circular resolution, from a phone,
// with no login (plan §2.9, §11).
//
// The vote is cast once. The server refuses a second attempt because a changeable
// audit trail is not an audit trail, so once a decision is recorded this page becomes
// a receipt rather than a form.
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";
import type { PortalConsent } from "@/types/secretarial";
import PortalShell from "./PortalShell.vue";

const route = useRoute();
const token = String(route.params.token ?? "");

const data = ref<PortalConsent | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const submitting = ref<string | null>(null);
const comments = ref("");

const RULE_LABEL: Record<string, string> = {
  majority: "Passes on a simple majority of the directors entitled to vote.",
  unanimous: "Passes only if every director entitled to vote consents.",
  two_thirds: "Passes on two-thirds of the directors entitled to vote.",
};

const DECISIONS = [
  { key: "consented", label: "I consent", cls: "bg-green-600 hover:bg-green-700" },
  { key: "declined", label: "I do not consent", cls: "bg-red-600 hover:bg-red-700" },
  { key: "abstained", label: "I abstain", cls: "bg-gray-500 hover:bg-gray-600" },
] as const;

// "pending" and "viewed" both mean nothing has been cast yet — only a real decision closes it.
const decided = computed(() =>
  Boolean(data.value && !["pending", "viewed"].includes(data.value.status)),
);

const expired = computed(() => {
  const at = data.value?.expires_at;
  return Boolean(at && new Date(at).getTime() < Date.now());
});

const DECISION_TEXT: Record<string, string> = {
  consented: "You consented to this resolution.",
  declined: "You did not consent to this resolution.",
  abstained: "You abstained from this resolution.",
};

async function load(): Promise<void> {
  loading.value = true;
  try {
    data.value = (await api.get<PortalConsent>(`/portal/r/${encodeURIComponent(token)}`)).data;
  } catch (e) {
    error.value = (e as ErrorEnvelope).detail ?? "This link is not valid.";
  } finally {
    loading.value = false;
  }
}

async function respond(decision: string): Promise<void> {
  submitting.value = decision;
  try {
    data.value = (
      await api.post<PortalConsent>(`/portal/r/${encodeURIComponent(token)}/respond`, {
        decision,
        comments: comments.value.trim() || null,
      })
    ).data;
  } catch (e) {
    error.value = (e as ErrorEnvelope).detail ?? "Could not record your response.";
  } finally {
    submitting.value = null;
  }
}

load();
</script>

<template>
  <PortalShell
    :entity-name="data?.entity_name"
    heading="Resolution by circulation"
    :loading="loading"
    :error="error"
  >
    <div v-if="data" class="space-y-4">
      <div class="rounded-lg bg-white p-5 shadow-sm">
        <h2 class="text-base font-semibold text-gray-900">{{ data.title }}</h2>
        <p class="mt-1 text-xs text-gray-400">
          <span v-if="data.reference_no">{{ data.reference_no }} · </span>For {{ data.recipient_name }}
        </p>

        <p v-if="data.description" class="mt-3 whitespace-pre-line text-sm text-gray-600">
          {{ data.description }}
        </p>

        <!-- The operative wording, set apart so it is unmistakably the thing being voted on. -->
        <blockquote
          class="mt-4 whitespace-pre-line border-l-4 border-gray-300 bg-gray-50 px-4 py-3 text-sm leading-relaxed text-gray-800"
        >
          {{ data.resolution_text }}
        </blockquote>

        <p class="mt-3 text-xs text-gray-500">{{ RULE_LABEL[data.consent_rule] }}</p>
        <p v-if="data.expires_at" class="mt-1 text-xs" :class="expired ? 'text-red-600' : 'text-gray-500'">
          {{ expired ? "Closed" : "Open until" }} {{ new Date(data.expires_at).toLocaleString() }}
        </p>
      </div>

      <!-- Receipt state -->
      <div v-if="decided" class="rounded-lg border border-green-200 bg-green-50 p-5 shadow-sm">
        <p class="text-sm font-medium text-green-900">{{ DECISION_TEXT[data.status] ?? data.status }}</p>
        <p v-if="data.responded_at" class="mt-1 text-xs text-green-800">
          Recorded {{ new Date(data.responded_at).toLocaleString() }}
        </p>
        <p class="mt-3 text-xs text-green-800">
          A recorded response cannot be changed here. If it is wrong, contact the company
          secretary — they will record a correction with a reason attached.
        </p>
      </div>

      <div v-else-if="expired" class="rounded-lg border border-gray-200 bg-white p-5 text-sm text-gray-600 shadow-sm">
        The window for responding to this resolution has closed.
      </div>

      <!-- Voting state -->
      <div v-else class="rounded-lg bg-white p-5 shadow-sm">
        <label class="mb-1 block text-xs uppercase tracking-wide text-gray-400" for="comments">
          Comments (optional)
        </label>
        <textarea
          id="comments"
          v-model="comments"
          rows="3"
          class="mb-4 w-full rounded border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          placeholder="Anything you want recorded alongside your decision"
        ></textarea>

        <div class="space-y-2">
          <button
            v-for="d in DECISIONS"
            :key="d.key"
            class="w-full rounded px-4 py-3 text-sm font-medium text-white disabled:opacity-50"
            :class="d.cls"
            :disabled="submitting !== null"
            @click="respond(d.key)"
          >
            {{ submitting === d.key ? "Recording…" : d.label }}
          </button>
        </div>
        <p class="mt-3 text-xs text-gray-500">Your response is recorded once and cannot be changed.</p>
      </div>
    </div>
  </PortalShell>
</template>
