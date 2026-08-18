<script setup lang="ts">
// The document library, plus the guided form that fills a pack.
//
// Documents are stored as inputs and a pinned template version, then re-rendered on
// download — so a minute book printed in 2031 still reads in the wording that was in
// force when it was signed. That is also why editing produces a new version rather than
// overwriting: the old one has to stay renderable.
import { computed, ref, watch } from "vue";
import { api, openPdf } from "@/api/client";
import { useSecretarialStore } from "@/stores/secretarial";
import type { ErrorEnvelope, ListResponse } from "@/types/core";
import type { ContentPack, SecretarialDocument } from "@/types/secretarial";

const store = useSecretarialStore();

const documents = ref<SecretarialDocument[]>([]);
const packs = ref<ContentPack[]>([]);
const versions = ref<SecretarialDocument[]>([]);
const openVersionsFor = ref<string | null>(null);
const previewHtml = ref<string | null>(null);

const loading = ref(false);
const error = ref<ErrorEnvelope | null>(null);
const busy = ref("");
const search = ref("");
const composing = ref(false);
const chosenPack = ref<ContentPack | null>(null);
const formData = ref<Record<string, string>>({});
const fragment = ref("resolution");

const STATUS_TONE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  final: "bg-blue-100 text-blue-800",
  issued: "bg-green-100 text-green-800",
  superseded: "bg-gray-200 text-gray-500",
};

// Only published packs can produce a document; showing the drafts as pickable would set
// up a failure two clicks later.
const usablePacks = computed(() => packs.value.filter((p) => p.review_status === "published"));
const draftPackCount = computed(() => packs.value.length - usablePacks.value.length);

const fragmentsOf = computed(() =>
  chosenPack.value?.fragments ? Object.keys(chosenPack.value.fragments) : [],
);

function fail(e: unknown): void {
  error.value = e as ErrorEnvelope;
}

async function load(): Promise<void> {
  if (!store.entityId) return;
  loading.value = true;
  error.value = null;
  try {
    documents.value = (
      await api.get<ListResponse<SecretarialDocument>>("/secretarial/documents", {
        params: { entity_id: store.entityId, search: search.value || undefined, page_size: 100 },
      })
    ).data.items;
    packs.value = (
      await api.get<ContentPack[]>("/secretarial/documents/packs", {
        params: { entity_kind: store.entity?.kind },
      })
    ).data;
  } catch (e) {
    fail(e);
  } finally {
    loading.value = false;
  }
}

function choosePack(pack: ContentPack): void {
  chosenPack.value = pack;
  formData.value = Object.fromEntries((pack.variables ?? []).map((v) => [v.name, ""]));
  const names = pack.fragments ? Object.keys(pack.fragments) : [];
  fragment.value = names.includes("resolution") ? "resolution" : (names[0] ?? "resolution");
}

async function generate(): Promise<void> {
  if (!store.entityId || !chosenPack.value) return;
  busy.value = "generate";
  error.value = null;
  try {
    await api.post("/secretarial/documents", {
      entity_id: store.entityId,
      pack_code: chosenPack.value.code,
      fragment: fragment.value,
      form_data: formData.value,
    });
    composing.value = false;
    chosenPack.value = null;
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function preview(doc: SecretarialDocument): Promise<void> {
  busy.value = doc.id;
  try {
    // The endpoint takes `fmt`, not the `format` the shared print modal assumes, so this
    // fetches the HTML itself rather than reusing PrintPreviewModal.
    const resp = await api.get(`/secretarial/documents/${doc.id}/download`, {
      params: { fmt: "html" },
      responseType: "text",
      transformResponse: (d) => d,
    });
    previewHtml.value = resp.data as string;
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

async function showVersions(doc: SecretarialDocument): Promise<void> {
  if (openVersionsFor.value === doc.id) {
    openVersionsFor.value = null;
    return;
  }
  try {
    versions.value = (
      await api.get<SecretarialDocument[]>(`/secretarial/documents/${doc.id}/versions`)
    ).data;
    openVersionsFor.value = doc.id;
  } catch (e) {
    fail(e);
  }
}

async function finalise(doc: SecretarialDocument): Promise<void> {
  busy.value = doc.id;
  error.value = null;
  try {
    await api.post(`/secretarial/documents/${doc.id}/finalise`, {});
    await load();
  } catch (e) {
    fail(e);
  } finally {
    busy.value = "";
  }
}

watch(() => store.entityId, load);
store.load().then(load);
</script>

<template>
  <div>
    <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-gray-900">Document library</h1>
        <p class="max-w-3xl text-sm text-gray-500">
          Resolutions, notices, minutes and letters, generated from reviewed templates. Each one
          keeps the inputs it was made from, so it re-renders identically years later.
        </p>
      </div>
      <button
        class="rounded bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
        @click="composing = !composing"
      >
        {{ composing ? "Cancel" : "New document" }}
      </button>
    </div>

    <p v-if="error" class="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
      {{ error.detail }}
    </p>

    <!-- Guided event form -->
    <section v-if="composing" class="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h2 class="mb-3 text-sm font-semibold text-gray-900">What happened?</h2>

      <div v-if="!chosenPack" class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <button
          v-for="p in usablePacks"
          :key="p.id"
          class="rounded border border-gray-200 p-3 text-left hover:border-gray-400 hover:bg-gray-50"
          @click="choosePack(p)"
        >
          <p class="text-sm font-medium text-gray-900">{{ p.title }}</p>
          <p class="text-xs text-gray-400">{{ p.source_ref || p.event_type }}</p>
        </button>
        <p v-if="!usablePacks.length" class="col-span-full py-6 text-center text-sm text-gray-400">
          No published templates yet. {{ draftPackCount }} are awaiting legal review.
        </p>
      </div>

      <div v-else>
        <div class="mb-3 flex items-center justify-between">
          <div>
            <p class="text-sm font-medium text-gray-900">{{ chosenPack.title }}</p>
            <p class="text-xs text-gray-400">{{ chosenPack.source_ref }}</p>
          </div>
          <button class="text-xs text-blue-600 hover:underline" @click="chosenPack = null">
            Choose a different template
          </button>
        </div>

        <div class="grid gap-3 sm:grid-cols-2">
          <label v-for="v in chosenPack.variables ?? []" :key="v.name" class="text-xs text-gray-500">
            {{ v.label || v.name }}<span v-if="v.required" class="text-red-500">*</span>
            <input
              v-model="formData[v.name]"
              class="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
            />
          </label>
        </div>

        <div class="mt-3 flex flex-wrap items-end gap-3">
          <label v-if="fragmentsOf.length > 1" class="text-xs text-gray-500">
            Produce
            <select v-model="fragment" class="mt-1 block rounded border border-gray-300 px-2 py-1.5 text-sm">
              <option v-for="f in fragmentsOf" :key="f" :value="f">{{ f.replace("_", " ") }}</option>
            </select>
          </label>
          <button
            class="rounded bg-gray-900 px-4 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-50"
            :disabled="busy !== ''"
            @click="generate"
          >
            {{ busy === "generate" ? "Generating…" : "Generate" }}
          </button>
        </div>
      </div>
    </section>

    <input
      v-model="search"
      placeholder="Search the library…"
      class="mb-3 w-full max-w-sm rounded border border-gray-300 px-3 py-1.5 text-sm"
      @keyup.enter="load"
    />

    <p v-if="loading" class="text-sm text-gray-500">Loading…</p>

    <div v-else class="overflow-x-auto rounded-lg border border-gray-200 bg-white shadow-sm">
      <table class="min-w-full divide-y divide-gray-200 text-sm">
        <thead class="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
          <tr>
            <th class="px-3 py-2">Document</th>
            <th class="px-3 py-2">Template</th>
            <th class="px-3 py-2">Version</th>
            <th class="px-3 py-2">State</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100">
          <template v-for="d in documents" :key="d.id">
            <tr class="hover:bg-gray-50">
              <td class="px-3 py-2">
                <p class="font-medium text-gray-900">{{ d.title }}</p>
                <p class="text-xs text-gray-400">{{ d.document_type }} · {{ d.fragment }}</p>
              </td>
              <td class="px-3 py-2 text-xs text-gray-500">
                {{ d.pack_code }}<span v-if="d.pack_version"> v{{ d.pack_version }}</span>
              </td>
              <td class="px-3 py-2 text-xs">
                <button class="text-blue-600 hover:underline" @click="showVersions(d)">
                  v{{ d.version }}<span v-if="d.version > 1"> ▾</span>
                </button>
              </td>
              <td class="px-3 py-2">
                <span class="rounded px-1.5 py-0.5 text-xs" :class="STATUS_TONE[d.status]">{{ d.status }}</span>
              </td>
              <td class="whitespace-nowrap px-3 py-2 text-right text-xs">
                <button class="text-blue-600 hover:underline" @click="preview(d)">Preview</button>
                <button
                  class="ml-3 text-blue-600 hover:underline"
                  @click="openPdf(`/secretarial/documents/${d.id}/download?fmt=pdf`)"
                >
                  PDF
                </button>
                <button
                  v-if="d.status === 'draft'"
                  class="ml-3 text-gray-600 hover:underline"
                  :disabled="busy !== ''"
                  @click="finalise(d)"
                >
                  Issue
                </button>
              </td>
            </tr>
            <tr v-if="openVersionsFor === d.id" class="bg-gray-50">
              <td colspan="5" class="px-3 py-2">
                <p class="mb-1 text-xs uppercase tracking-wide text-gray-400">Version history</p>
                <ul class="space-y-1">
                  <li v-for="v in versions" :key="v.id" class="text-xs text-gray-600">
                    v{{ v.version }} · {{ v.status }} ·
                    {{ new Date(v.creation).toLocaleString() }}
                    <span v-if="v.change_summary"> — {{ v.change_summary }}</span>
                    <span v-if="v.is_current" class="ml-1 text-green-700">(current)</span>
                  </li>
                </ul>
              </td>
            </tr>
          </template>
          <tr v-if="!documents.length">
            <td colspan="5" class="px-3 py-8 text-center text-sm text-gray-400">
              Nothing generated yet.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Preview overlay -->
    <div
      v-if="previewHtml"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="previewHtml = null"
    >
      <div class="flex h-full w-full max-w-4xl flex-col rounded-lg bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-200 px-4 py-2">
          <h3 class="text-sm font-semibold text-gray-900">Preview</h3>
          <button class="text-sm text-gray-500 hover:text-gray-900" @click="previewHtml = null">Close</button>
        </div>
        <iframe :srcdoc="previewHtml" class="h-full w-full rounded-b-lg" title="Document preview"></iframe>
      </div>
    </div>
  </div>
</template>
