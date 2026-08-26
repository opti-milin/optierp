<script setup lang="ts">
// The export instructions, in the app, next to the wizard that consumes them.
//
// The same content ships as a PDF (docs/tally/Tally-Export-Guide.pdf) to send to
// a client's accounts team before they export. This page is for whoever is doing
// the import here — so the "why" is stated in terms of what the importer needs,
// and every step names the failure it prevents.
import { ref } from "vue";

const openStep = ref<string | null>("masters");
function toggle(key: string): void {
  openStep.value = openStep.value === key ? null : key;
}
</script>

<template>
  <div>
    <div class="mb-4">
      <RouterLink to="/data-migration/imports" class="text-sm text-blue-600 hover:underline">
        ← All imports
      </RouterLink>
      <h1 class="text-xl font-semibold text-gray-900">Exporting from Tally</h1>
      <p class="max-w-3xl text-sm text-gray-500">
        What to ask for, and how to tell a good export from a bad one before you
        spend time importing it. There is a printable version of this to send to a
        client's accounts team —
        <a href="/docs/tally/Tally-Export-Guide.pdf" class="text-blue-600 hover:underline">
          download the PDF guide</a>.
      </p>
    </div>

    <!-- The one that costs the most time when it goes wrong. -->
    <div class="mb-6 rounded-lg border-l-4 border-red-400 bg-red-50 p-4">
      <h2 class="text-sm font-semibold text-red-900">Two files, in this order</h2>
      <p class="mt-1 text-sm text-red-800">
        Masters first, then transactions. A transactions file on its own is unusable:
        every ledger and item it names will be unrecognised, because the names it
        refers to have nothing to point at yet.
      </p>
    </div>

    <div class="space-y-3">
      <!-- Step 1 -->
      <div class="rounded-lg border border-gray-200 bg-white">
        <button
          type="button"
          class="flex w-full items-center justify-between px-4 py-3 text-left"
          @click="toggle('masters')"
        >
          <span class="font-medium text-gray-900">1 · Export the masters</span>
          <span class="text-xs text-gray-400">{{ openStep === "masters" ? "▲" : "▼" }}</span>
        </button>
        <div v-if="openStep === 'masters'" class="border-t border-gray-100 px-4 py-3 text-sm">
          <ol class="ml-4 list-decimal space-y-1 text-gray-700">
            <li>From the <strong>Gateway of Tally</strong>, press <kbd>Alt</kbd>+<kbd>E</kbd>.</li>
            <li>Choose <strong>Masters</strong>.</li>
            <li>
              Set <strong>Type of Masters</strong> to <strong>All Masters</strong>.
            </li>
            <li>Set <strong>File Format</strong> to <strong>XML</strong>.</li>
            <li>Name it <code>Company-Masters.xml</code> and export.</li>
          </ol>
          <p class="mt-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
            <strong>Check step 3 carefully.</strong> If it says
            <em>Accounting Masters</em> you get the ledgers but <strong>no stock
            items</strong> — every product on every invoice then imports as unknown.
            This is the most common bad export we see.
          </p>
        </div>
      </div>

      <!-- Step 2 -->
      <div class="rounded-lg border border-gray-200 bg-white">
        <button
          type="button"
          class="flex w-full items-center justify-between px-4 py-3 text-left"
          @click="toggle('vouchers')"
        >
          <span class="font-medium text-gray-900">2 · Export the transactions</span>
          <span class="text-xs text-gray-400">{{ openStep === "vouchers" ? "▲" : "▼" }}</span>
        </button>
        <div v-if="openStep === 'vouchers'" class="border-t border-gray-100 px-4 py-3 text-sm">
          <ol class="ml-4 list-decimal space-y-1 text-gray-700">
            <li>
              Gateway of Tally → <strong>Display More Reports</strong> →
              <strong>Day Book</strong>.
            </li>
            <li>
              Press <kbd>Alt</kbd>+<kbd>F2</kbd> and set the <strong>from and to
              dates</strong> for the period being moved.
            </li>
            <li>Press <kbd>Alt</kbd>+<kbd>E</kbd> and choose <strong>Current</strong>.</li>
            <li>Set <strong>File Format</strong> to <strong>XML</strong>.</li>
            <li>Name it with the period, e.g. <code>Company-Txns-Apr25-Mar26.xml</code>.</li>
          </ol>
          <p class="mt-3 text-xs text-gray-600">
            Tally exports only what is on screen, so the Day Book period <em>is</em>
            the export period. Over roughly 10,000 vouchers, export a quarter at a
            time — smaller files are easier to check and cheaper to redo.
          </p>
        </div>
      </div>

      <!-- Step 3 -->
      <div class="rounded-lg border border-gray-200 bg-white">
        <button
          type="button"
          class="flex w-full items-center justify-between px-4 py-3 text-left"
          @click="toggle('check')"
        >
          <span class="font-medium text-gray-900">3 · Check the file before importing</span>
          <span class="text-xs text-gray-400">{{ openStep === "check" ? "▲" : "▼" }}</span>
        </button>
        <div v-if="openStep === 'check'" class="border-t border-gray-100 px-4 py-3 text-sm">
          <p class="mb-2 text-gray-700">
            Open each file in a text editor and search (<kbd>Ctrl</kbd>+<kbd>F</kbd>):
          </p>
          <table class="w-full text-left text-xs">
            <thead class="text-gray-500">
              <tr>
                <th class="py-1 font-medium">Search for</th>
                <th class="py-1 font-medium">In</th>
                <th class="py-1 font-medium">If missing</th>
              </tr>
            </thead>
            <tbody class="text-gray-700">
              <tr class="border-t border-gray-100">
                <td class="py-1"><code>&lt;STOCKITEM</code></td>
                <td class="py-1">Masters</td>
                <td class="py-1">Exported as Accounting Masters — redo as All Masters</td>
              </tr>
              <tr class="border-t border-gray-100">
                <td class="py-1"><code>&lt;LEDGER</code></td>
                <td class="py-1">Masters</td>
                <td class="py-1">Wrong export type entirely</td>
              </tr>
              <tr class="border-t border-gray-100">
                <td class="py-1"><code>OPENINGBALANCE</code></td>
                <td class="py-1">Masters</td>
                <td class="py-1">No opening balances — imported books will start at zero</td>
              </tr>
              <tr class="border-t border-gray-100">
                <td class="py-1"><code>&lt;VOUCHER</code></td>
                <td class="py-1">Transactions</td>
                <td class="py-1">Empty Day Book period</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Step 4 -->
      <div class="rounded-lg border border-gray-200 bg-white">
        <button
          type="button"
          class="flex w-full items-center justify-between px-4 py-3 text-left"
          @click="toggle('import')"
        >
          <span class="font-medium text-gray-900">4 · Import here</span>
          <span class="text-xs text-gray-400">{{ openStep === "import" ? "▲" : "▼" }}</span>
        </button>
        <div v-if="openStep === 'import'" class="border-t border-gray-100 px-4 py-3 text-sm">
          <ol class="ml-4 list-decimal space-y-1 text-gray-700">
            <li>
              Upload the <strong>masters</strong> file and set the
              <strong>migration cut-off date</strong> — opening balances are booked
              on that date, and without it they are skipped.
            </li>
            <li><strong>Auto-map</strong>, then review anything with low confidence.</li>
            <li>
              <strong>Dry run.</strong> Nothing is written. Check the planned counts,
              the unmapped names, and whether anything is reported as already imported.
            </li>
            <li><strong>Run</strong>, then compare the totals against Tally.</li>
            <li>Then repeat 1–4 for the <strong>transactions</strong> file.</li>
          </ol>
          <p class="mt-3 text-xs text-gray-600">
            Numbers not matching? <strong>Roll back</strong>, fix the mapping and run
            again. Rollback cancels with reversing entries rather than deleting, so
            the audit trail shows what the import did and undid. Importing the same
            period twice is safe — vouchers you already have are recognised and skipped.
          </p>
        </div>
      </div>
    </div>

    <div class="mt-6 rounded-lg border border-gray-200 bg-gray-50 p-4 text-sm">
      <h2 class="font-medium text-gray-900">Known limits</h2>
      <p class="mt-1 text-gray-600">
        Stated plainly, because a migration tool that overstates itself is worse than
        one that does not exist.
      </p>
      <ul class="mt-2 ml-4 list-disc space-y-1 text-gray-700">
        <li><strong>Payroll</strong> — no HR module here; counted and reported, not imported.</li>
        <li>
          <strong>Opening bill-wise ageing</strong> — a party's opening balance imports
          as one figure against the party. The total and the party are right; the
          per-invoice ageing buckets are not.
        </li>
        <li><strong>Cost categories</strong> — Tally slices costs two ways; we have one cost-centre tree.</li>
        <li><strong>Quantity-slab pricing</strong> — collapses to the lowest slab, and says so.</li>
        <li>
          <strong>CSV exports</strong> — carry no GUIDs, bill references, godowns or
          <code>ALTERID</code>, so duplicate and amendment detection cannot work on
          them. Use XML for anything you may import more than once.
        </li>
      </ul>
      <p class="mt-2 text-xs text-gray-500">
        Every entity, and what it becomes, is listed on the
        <RouterLink to="/data-migration/coverage" class="text-blue-600 hover:underline">
          coverage page</RouterLink>.
      </p>
    </div>
  </div>
</template>
