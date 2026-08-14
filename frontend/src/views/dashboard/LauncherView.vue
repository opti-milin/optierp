<script setup lang="ts">
// Module launcher — the ERPNext-style home: a grid of module tiles. Each tile
// opens that module's workspace. Rendered as the home ("/"); the AppShell hides
// its sidebar on this route so the launcher is full-page.

import { computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { brand } from "@/brand";
import { useAuthStore } from "@/stores/auth";
import { useModuleFlagsStore } from "@/stores/moduleFlags";

interface Tile {
  label: string;
  to: string;
  icon: string;
  flag?: "manufacturing" | "taxation";
}

const ALL_TILES: Tile[] = [
  { label: "Sales", to: "/selling", icon: "🧭" },
  { label: "Purchase", to: "/buying", icon: "🛍" },
  { label: "Inventory", to: "/stock", icon: "📦" },
  { label: "Manufacturing", to: "/manufacturing", icon: "🏭", flag: "manufacturing" },
  { label: "Accounting", to: "/accounting", icon: "📊" },
  { label: "Taxation", to: "/taxation", icon: "🧾", flag: "taxation" },
  { label: "Assets", to: "/assets", icon: "🏗" },
  { label: "Setup", to: "/companies", icon: "⚙" },
  { label: "Reports", to: "/reports", icon: "📈" },
];

const router = useRouter();
const auth = useAuthStore();
const flags = useModuleFlagsStore();

const tiles = computed(() =>
  ALL_TILES.filter((t) => !t.flag || flags.flags[t.flag] !== false),
);

onMounted(() => {
  void flags.load();
});

function open(tile: Tile): void {
  void router.push(tile.to);
}
async function logout(): Promise<void> {
  await auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div>
    <header class="mb-12 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <img :src="brand.logo_url" :alt="brand.product_name" class="h-8 w-8" />
        <span class="text-lg font-semibold text-gray-900">{{ brand.product_name }}</span>
      </div>
      <div class="flex items-center gap-3 text-sm text-gray-500">
        <span class="truncate">{{ auth.email }}</span>
        <button class="font-medium text-primary hover:underline" @click="logout">Sign out</button>
      </div>
    </header>

    <div class="mx-auto grid max-w-4xl grid-cols-2 gap-6 sm:grid-cols-3 lg:grid-cols-4">
      <button
        v-for="tile in tiles"
        :key="tile.label"
        type="button"
        class="flex flex-col items-center gap-3 rounded-xl border border-gray-200 bg-white p-6 shadow-sm transition hover:border-primary hover:shadow"
        @click="open(tile)"
      >
        <span class="flex h-14 w-14 items-center justify-center rounded-xl bg-primary/10 text-2xl">
          {{ tile.icon }}
        </span>
        <span class="text-sm font-medium text-gray-800">{{ tile.label }}</span>
      </button>
    </div>
  </div>
</template>
