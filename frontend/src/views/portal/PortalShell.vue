<script setup lang="ts">
// The frame every portal page sits in.
//
// A director opening this has no account, is probably on a phone, and has very likely
// never seen this product before. So: no navigation, no branding we cannot justify,
// one column, large type, and the company's own name at the top so they can tell at a
// glance which of their boards this is about.
defineProps<{
  entityName?: string;
  heading: string;
  loading?: boolean;
  error?: string | null;
}>();
</script>

<template>
  <div class="min-h-screen bg-gray-100 px-4 py-6 sm:py-10">
    <div class="mx-auto w-full max-w-2xl">
      <header class="mb-5">
        <p v-if="entityName" class="text-sm font-medium uppercase tracking-wide text-gray-500">
          {{ entityName }}
        </p>
        <h1 class="text-xl font-semibold text-gray-900 sm:text-2xl">{{ heading }}</h1>
      </header>

      <p v-if="loading" class="rounded-lg bg-white p-6 text-center text-sm text-gray-500 shadow-sm">
        Loading…
      </p>

      <!-- Token failures land here. The message comes from the server and is deliberately
           vague about *why*; what the reader needs is the next step, not a diagnosis. -->
      <div
        v-else-if="error"
        class="rounded-lg border border-amber-200 bg-amber-50 p-6 text-sm text-amber-900 shadow-sm"
      >
        <p class="font-medium">{{ error }}</p>
        <p class="mt-2 text-amber-800">
          If you believe this link should work, contact the company secretary who sent it.
        </p>
      </div>

      <slot v-else />

      <p class="mt-8 text-center text-xs text-gray-400">
        This link is personal to you and records when it was opened.
      </p>
    </div>
  </div>
</template>
