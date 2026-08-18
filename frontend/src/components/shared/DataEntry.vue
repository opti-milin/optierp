<script setup lang="ts">
// "Data Entry" control for transaction line items. Manual entry is the grid
// itself; this adds the other two modes:
//   • Import — from a CSV template or a Tally CSV export
//   • OCR    — upload an invoice/PO; extraction runs through the API
// Emits parsed rows; the parent maps them to catalog items.

import { ref, watch } from "vue";
import { api } from "@/api/client";
import type { ErrorEnvelope } from "@/types/core";

export interface ImportedRow {
  item_code: string;
  qty: number;
  rate?: number;
}

interface OcrLine {
  item_code: string;
  item_name: string;
  description: string;
  qty: number | string;
  rate: number | string | null;
  uom: string | null;
  matched: boolean;
  match_confidence: number;
}

interface OcrExtractResponse {
  lines: OcrLine[];
  warnings: string[];
  document_type: string | null;
  party_name: string | null;
  document_number: string | null;
  document_date: string | null;
  provider: string;
  model: string;
}

interface ReviewRow {
  item_code: string;
  description: string;
  qty: number;
  rate: number | "";
  matched: boolean;
  include: boolean;
}

const emit = defineEmits<{ import: [rows: ImportedRow[]] }>();

const open = ref(false);
const mode = ref<"csv" | "tally" | "ocr">("csv");
const note = ref<string | null>(null);

const ocrConfigured = ref<boolean | null>(null);
const ocrFile = ref<File | null>(null);
const ocrBusy = ref(false);
const ocrError = ref<string | null>(null);
const ocrMeta = ref<string | null>(null);
const ocrWarnings = ref<string[]>([]);
const reviewRows = ref<ReviewRow[]>([]);

const OCR_MAX_BYTES = 10 * 1024 * 1024;

function resetOcr(): void {
  ocrFile.value = null;
  ocrBusy.value = false;
  ocrError.value = null;
  ocrMeta.value = null;
  ocrWarnings.value = [];
  reviewRows.value = [];
}

function close(): void {
  open.value = false;
  note.value = null;
  resetOcr();
}

async function loadOcrStatus(): Promise<void> {
  try {
    const { data } = await api.get<{ configured: boolean }>("/ocr/status");
    ocrConfigured.value = data.configured;
  } catch {
    ocrConfigured.value = null;
  }
}

watch(open, (isOpen) => {
  if (isOpen) void loadOcrStatus();
});

watch(mode, (m) => {
  if (m === "ocr") void loadOcrStatus();
});

// Split one CSV line, honouring double-quoted fields (with "" escapes) so an
// embedded comma stays in a single cell.
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i += 1; }
        else inQuotes = false;
      } else cur += ch;
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out.map((c) => c.trim());
}

// Header row decides columns; expects item_code, with optional qty, rate.
function parseCsv(text: string): ImportedRow[] {
  const clean = text.replace(/^﻿/, ""); // strip UTF-8 BOM (Excel/Tally exports)
  const lines = clean.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (lines.length < 2) return [];
  const header = splitCsvLine(lines[0]).map((h) => h.toLowerCase());
  const ci = header.indexOf("item_code");
  const qi = header.indexOf("qty");
  const ri = header.indexOf("rate");
  if (ci === -1) return [];
  const rows: ImportedRow[] = [];
  for (const line of lines.slice(1)) {
    const cells = splitCsvLine(line);
    const item_code = cells[ci];
    if (!item_code) continue;
    rows.push({
      item_code,
      qty: qi !== -1 && cells[qi] ? Number(cells[qi]) : 1,
      rate: ri !== -1 && cells[ri] ? Number(cells[ri]) : undefined,
    });
  }
  return rows;
}

async function onFile(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const text = await file.text();
  const rows = parseCsv(text);
  input.value = ""; // reset so re-selecting the same file fires change again
  if (rows.length === 0) {
    note.value = "No valid rows found. The file needs an `item_code` column (with optional `qty`, `rate`).";
    return;
  }
  emit("import", rows);
  note.value = null;
  close();
}

function downloadTemplate(): void {
  const csv = "item_code,qty,rate\nITEM-001,1,100\n";
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "items-template.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function onOcrFile(e: Event): void {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  ocrFile.value = file;
  ocrError.value = null;
  ocrMeta.value = null;
  ocrWarnings.value = [];
  reviewRows.value = [];
  if (file && file.size > OCR_MAX_BYTES) {
    ocrFile.value = null;
    input.value = "";
    ocrError.value = "That file is larger than 10 MB. Use a smaller scan or a single page.";
  }
}

async function extractOcr(): Promise<void> {
  const file = ocrFile.value;
  if (!file || ocrBusy.value) return;
  ocrBusy.value = true;
  ocrError.value = null;
  ocrWarnings.value = [];
  reviewRows.value = [];
  ocrMeta.value = null;
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const { data } = await api.post<OcrExtractResponse>(
      "/ocr/extract",
      {
        file_name: file.name,
        content_base64: bytesToBase64(bytes),
        mime_type: file.type || null,
      },
      { timeout: 120_000 },
    );
    const bits = [data.party_name, data.document_number, data.document_date].filter(Boolean);
    ocrMeta.value = bits.length ? bits.join(" · ") : null;
    ocrWarnings.value = data.warnings ?? [];
    reviewRows.value = data.lines.map((line) => ({
      item_code: line.item_code,
      description: line.description,
      qty: Number(line.qty) || 1,
      rate: line.rate == null || line.rate === "" ? "" : Number(line.rate),
      matched: line.matched,
      include: true,
    }));
    if (data.lines.length === 0 && ocrWarnings.value.length === 0) {
      ocrError.value = "No line items were found. Try a clearer scan of the item table.";
    }
  } catch (e) {
    const err = e as ErrorEnvelope;
    ocrError.value = err.detail || "OCR extraction failed.";
    if (err.code === "ocr_not_configured") {
      ocrConfigured.value = false;
    }
  } finally {
    ocrBusy.value = false;
  }
}

function applyOcr(): void {
  const rows: ImportedRow[] = [];
  for (const row of reviewRows.value) {
    if (!row.include || !row.item_code.trim()) continue;
    rows.push({
      item_code: row.item_code.trim(),
      qty: Number(row.qty) || 1,
      rate: row.rate === "" ? undefined : Number(row.rate),
    });
  }
  if (rows.length === 0) {
    ocrError.value = "Select at least one line with an item code.";
    return;
  }
  emit("import", rows);
  close();
}
</script>

<template>
  <div class="relative inline-block">
    <button type="button" class="btn-secondary gap-1" @click="open = !open">
      Import <span class="text-xs text-gray-400">▾</span>
    </button>

    <div v-if="open" class="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" @click.self="close">
      <div class="w-full max-w-2xl rounded-lg bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h3 class="text-sm font-semibold text-gray-900">Import items</h3>
          <button type="button" class="text-gray-400 hover:text-gray-700" @click="close">✕</button>
        </div>

        <!-- mode tabs -->
        <div class="flex gap-4 border-b border-gray-200 px-5">
          <button
            v-for="m in (['csv', 'tally', 'ocr'] as const)"
            :key="m"
            type="button"
            class="-mb-px border-b-2 py-2 text-sm font-medium capitalize"
            :class="mode === m ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'"
            @click="mode = m; note = null"
          >
            {{ m === 'ocr' ? 'OCR' : m }}
          </button>
        </div>

        <div class="space-y-3 px-5 py-4 text-sm">
          <!-- CSV -->
          <template v-if="mode === 'csv'">
            <p class="text-gray-600">Upload a CSV with columns <code>item_code, qty, rate</code>.</p>
            <button type="button" class="text-primary hover:underline" @click="downloadTemplate">
              ↓ Download template
            </button>
            <input type="file" accept=".csv,text/csv" class="block w-full text-sm" @change="onFile" />
          </template>

          <!-- Tally -->
          <template v-else-if="mode === 'tally'">
            <p class="text-gray-600">
              In Tally: <em>Gateway of Tally → Display → Export</em> to <strong>CSV</strong>, then upload it here
              (same <code>item_code, qty, rate</code> columns).
            </p>
            <input type="file" accept=".csv,text/csv" class="block w-full text-sm" @change="onFile" />
            <p class="text-xs text-gray-400">Direct Tally XML import connects via API (coming soon).</p>
          </template>

          <!-- OCR -->
          <template v-else>
            <p class="text-gray-600">Upload a scanned invoice or PO; line items are extracted automatically.</p>
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif,application/pdf"
              class="block w-full text-sm"
              :disabled="ocrBusy"
              @change="onOcrFile"
            />
            <div class="flex flex-wrap items-center gap-2">
              <button
                type="button"
                class="btn-primary"
                :disabled="!ocrFile || ocrBusy || ocrConfigured === false"
                @click="extractOcr"
              >
                {{ ocrBusy ? "Extracting…" : "Extract" }}
              </button>
              <p v-if="ocrConfigured === false" class="text-xs text-amber-700">
                OCR is not configured. Set <code>OCR_API_KEY</code> on the API (OpenAI-compatible vision) and restart.
              </p>
            </div>
            <p v-if="ocrMeta" class="text-xs text-gray-500">{{ ocrMeta }}</p>
            <p v-for="w in ocrWarnings" :key="w" class="text-xs text-amber-700">{{ w }}</p>
            <p v-if="ocrError" class="text-sm text-red-600">{{ ocrError }}</p>

            <div v-if="reviewRows.length" class="overflow-x-auto rounded border border-gray-200">
              <table class="min-w-full text-left text-xs">
                <thead class="bg-gray-50 text-gray-600">
                  <tr>
                    <th class="px-2 py-1.5 font-medium">Add</th>
                    <th class="px-2 py-1.5 font-medium">Item</th>
                    <th class="px-2 py-1.5 font-medium">Description</th>
                    <th class="px-2 py-1.5 font-medium text-right">Qty</th>
                    <th class="px-2 py-1.5 font-medium text-right">Rate</th>
                    <th class="px-2 py-1.5 font-medium">Match</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, idx) in reviewRows" :key="idx" class="border-t border-gray-100">
                    <td class="px-2 py-1">
                      <input v-model="row.include" type="checkbox" />
                    </td>
                    <td class="px-2 py-1">
                      <input v-model="row.item_code" type="text" class="form-input py-1 text-xs" />
                    </td>
                    <td class="max-w-[12rem] truncate px-2 py-1 text-gray-600" :title="row.description">
                      {{ row.description }}
                    </td>
                    <td class="px-2 py-1">
                      <input v-model.number="row.qty" type="number" min="0" step="any" class="form-input py-1 text-right text-xs" />
                    </td>
                    <td class="px-2 py-1">
                      <input v-model="row.rate" type="number" min="0" step="any" class="form-input py-1 text-right text-xs" />
                    </td>
                    <td class="whitespace-nowrap px-2 py-1">
                      <span v-if="row.matched" class="text-emerald-700">Catalog</span>
                      <span v-else class="text-amber-700">Unmatched</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <button
              v-if="reviewRows.length"
              type="button"
              class="btn-primary"
              @click="applyOcr"
            >
              Add selected to document
            </button>
          </template>

          <p v-if="note" class="text-sm text-red-600">{{ note }}</p>
        </div>
      </div>
    </div>
  </div>
</template>
