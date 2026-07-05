<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { brand } from "@/brand";
import { WORKSPACES, type WsNavGroup } from "@/config/workspaces";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

// Global sidebar (shown outside any module — e.g. Setup pages).
const GLOBAL_NAV: WsNavGroup[] = [
  {
    items: [
      { label: "Home", to: "/", icon: "⌂" },
      { label: "Sales", to: "/selling", icon: "🧭" },
      { label: "Purchases", to: "/buying", icon: "🛍" },
      { label: "Inventory", to: "/stock", icon: "📦" },
      { label: "Accounting", to: "/accounting", icon: "📊" },
    ],
  },
  {
    title: "Setup",
    items: [
      { label: "Companies", to: "/companies", icon: "🏢" },
      { label: "Users", to: "/users", icon: "👤" },
      { label: "Roles", to: "/roles", icon: "🛡" },
      { label: "Settings", to: "/settings", icon: "⚙" },
    ],
  },
];

const MODULE_KEYS = Object.keys(WORKSPACES);
const GLOBAL_PREFIXES = ["/companies", "/users", "/roles", "/settings"];

// Which module(s) list each route path (from the workspace configs). A path in
// exactly one module is "owned" by it; a path in several (Item, Reports, Sales
// Invoice, Delivery Note...) is shared.
const ROUTE_OWNERS: Record<string, Set<string>> = {};
function addOwner(path: string, key: string): void {
  (ROUTE_OWNERS[path] ??= new Set()).add(key);
}
for (const [key, cfg] of Object.entries(WORKSPACES)) {
  addOwner(`/${key}`, key);
  for (const group of cfg.sidebar) {
    for (const item of group.items) if (item.to !== "/") addOwner(item.to, key);
  }
}

function ownersForPath(path: string): Set<string> {
  const owners = new Set<string>();
  for (const [p, set] of Object.entries(ROUTE_OWNERS)) {
    if (path === p || path.startsWith(`${p}/`)) set.forEach((m) => owners.add(m));
  }
  return owners;
}

// Sticky module context (mirrors ERPNext workspaces): entering a module keeps its
// sidebar as you move through its pages — including shared pages like Item — until
// you go Home, open a Setup page, or open a page owned by ONE other module.
const currentModule = ref<string | null>(null);

function updateModule(path: string): void {
  const root = MODULE_KEYS.find((k) => path === `/${k}` || path.startsWith(`/${k}/`));
  if (root) {
    currentModule.value = root;
  } else if (path === "/" || GLOBAL_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`))) {
    currentModule.value = null;
  } else if (path.startsWith("/m/")) {
    currentModule.value = currentModule.value ?? "selling"; // engine masters are Selling-group
  } else {
    const owners = ownersForPath(path);
    if (owners.size === 1) {
      currentModule.value = [...owners][0]; // belongs to exactly one module -> switch
    } else if (currentModule.value === null) {
      currentModule.value = owners.size ? [...owners][0] : null; // deep-link guess
    }
    // shared page with an existing context -> keep it
  }
}
watch(() => route.path, updateModule, { immediate: true });

const isHome = computed(() => route.name === "dashboard"); // launcher: full-page, no sidebar
const sidebarGroups = computed<WsNavGroup[]>(() =>
  currentModule.value ? WORKSPACES[currentModule.value].sidebar : GLOBAL_NAV,
);
const headerTitle = computed(() =>
  currentModule.value ? WORKSPACES[currentModule.value].title : brand.value.product_name,
);
const headerSubtitle = computed(() =>
  currentModule.value ? brand.value.product_name : brand.value.tagline,
);

function isActive(to: string): boolean {
  if (to === "/") return route.path === "/";
  return route.path === to || route.path.startsWith(`${to}/`);
}

async function logout(): Promise<void> {
  await auth.logout();
  void router.push({ name: "login" });
}
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <aside v-if="!isHome" class="flex w-60 flex-col border-r border-gray-200 bg-white">
      <div class="flex items-center gap-3 border-b border-gray-200 px-4 py-4">
        <img :src="brand.logo_url" :alt="brand.product_name" class="h-8 w-8" />
        <div>
          <div class="text-sm font-semibold text-gray-900">{{ headerTitle }}</div>
          <div class="text-xs text-gray-500">{{ headerSubtitle }}</div>
        </div>
      </div>
      <nav class="sidebar-scroll flex-1 overflow-y-scroll scroll-smooth p-3">
        <div v-for="(group, gi) in sidebarGroups" :key="gi" class="mb-2">
          <div
            v-if="group.title"
            class="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-gray-400"
          >
            {{ group.title }}
          </div>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            class="flex items-center gap-3 rounded-md px-3 py-1.5 text-sm font-medium"
            :class="
              isActive(item.to)
                ? 'bg-primary/10 text-primary'
                : 'text-gray-600 hover:bg-gray-100'
            "
          >
            <span v-if="item.icon" aria-hidden="true">{{ item.icon }}</span>
            {{ item.label }}
          </RouterLink>
        </div>
      </nav>
      <div class="border-t border-gray-200 p-4">
        <div class="text-sm font-medium text-gray-900">{{ auth.fullName }}</div>
        <div class="truncate text-xs text-gray-500">{{ auth.email }}</div>
        <button class="mt-2 text-xs font-medium text-primary hover:underline" @click="logout">
          Sign out
        </button>
      </div>
    </aside>
    <main class="flex-1 overflow-y-auto p-6">
      <!-- keyed by fullPath: reusing a component instance across e.g.
           /quotations/:id -> /sales-orders/new would keep stale state -->
      <RouterView :key="route.fullPath" />
    </main>
  </div>
</template>

<style scoped>
/* Always-visible, thin styled scrollbar for the sidebar nav */
.sidebar-scroll {
  scrollbar-width: thin; /* Firefox */
  scrollbar-color: #cbd5e1 transparent; /* thumb track (Firefox) */
}
.sidebar-scroll::-webkit-scrollbar {
  width: 8px;
}
.sidebar-scroll::-webkit-scrollbar-track {
  background: transparent;
}
.sidebar-scroll::-webkit-scrollbar-thumb {
  background-color: #cbd5e1; /* slate-300 */
  border-radius: 9999px;
}
.sidebar-scroll::-webkit-scrollbar-thumb:hover {
  background-color: #94a3b8; /* slate-400 */
}
</style>
