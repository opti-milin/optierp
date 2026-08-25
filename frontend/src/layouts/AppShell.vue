<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { brand } from "@/brand";
import DelegatedBanner from "@/components/secretarial/DelegatedBanner.vue";
import EntitySwitcher from "@/components/secretarial/EntitySwitcher.vue";
import { WORKSPACES, type WsNavGroup } from "@/config/workspaces";
import { useAuthStore } from "@/stores/auth";
import { useModuleFlagsStore } from "@/stores/moduleFlags";
import { useSecretarialStore } from "@/stores/secretarial";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const flags = useModuleFlagsStore();
const secretarial = useSecretarialStore();

const SIDEBAR_COLLAPSED_KEY = "optireach.sidebarCollapsed";
const sidebarCollapsed = ref(false);

onMounted(() => {
  try {
    sidebarCollapsed.value = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
  } catch {
    sidebarCollapsed.value = false;
  }
  void flags.load();
});

function toggleSidebar(): void {
  sidebarCollapsed.value = !sidebarCollapsed.value;
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed.value ? "1" : "0");
  } catch {
    /* private mode / blocked storage — collapse still works for the session */
  }
}

// Global sidebar (shown outside any module — e.g. Setup pages).
const GLOBAL_NAV: WsNavGroup[] = [
  {
    items: [
      { label: "Home", to: "/", icon: "⌂" },
      { label: "Sales", to: "/selling", icon: "🧭" },
      { label: "Purchases", to: "/buying", icon: "🛍" },
      { label: "Inventory", to: "/stock", icon: "📦" },
      { label: "Manufacturing", to: "/manufacturing", icon: "🏭" },
      { label: "Accounting", to: "/accounting", icon: "📊" },
      { label: "Taxation", to: "/taxation", icon: "🧾" },
      { label: "Secretarial", to: "/secretarial", icon: "⚖" },
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

const filteredGlobalNav = computed<WsNavGroup[]>(() => {
  return GLOBAL_NAV.map((g) => ({
    ...g,
    items: g.items.filter((i) => {
      if (i.to === "/manufacturing" && !flags.flags.manufacturing) return false;
      if (i.to === "/taxation" && !flags.flags.taxation) return false;
      // Module 13 is opt-in — most tenants keeping books here do not run their
      // own secretarial function, and an unused module in the nav is noise.
      if (i.to === "/secretarial" && !flags.flags.secretarial) return false;
      return true;
    }),
  }));
});

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
    // Engine masters: prefer the unique owning module (e.g. Tax Policy → Taxation).
    // Shared masters keep sticky context; cold deep-links fall back to selling.
    const owners = ownersForPath(path);
    if (owners.size === 1) {
      currentModule.value = [...owners][0];
    } else if (currentModule.value && (owners.size === 0 || owners.has(currentModule.value))) {
      // keep sticky module
    } else if (currentModule.value === null) {
      currentModule.value = owners.size ? [...owners][0] : "selling";
    }
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
const sidebarGroups = computed<WsNavGroup[]>(() => {
  if (!currentModule.value) return filteredGlobalNav.value;
  const groups = WORKSPACES[currentModule.value].sidebar;
  // Secretarial ships one menu for two audiences. A company that sells
  // appliances has no clients; a CS practice has no single "my company".
  // Showing either the other's vocabulary is the fastest way to confuse both.
  const profile = secretarial.settings?.profile ?? "business";
  return groups
    .map((g) => ({ ...g, items: g.items.filter((i) => !i.profiles || i.profiles.includes(profile)) }))
    .filter((g) => g.items.length > 0);
});
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
    <aside
      v-if="!isHome"
      class="flex shrink-0 flex-col border-r border-gray-200 bg-white transition-[width] duration-200 ease-out"
      :class="sidebarCollapsed ? 'w-16' : 'w-60'"
      :aria-expanded="!sidebarCollapsed"
    >
      <div
        class="flex items-center border-b border-gray-200 py-3"
        :class="sidebarCollapsed ? 'flex-col gap-2 px-2' : 'gap-3 px-3'"
      >
        <img
          :src="brand.logo_url"
          :alt="brand.product_name"
          class="h-8 w-8 shrink-0"
        />
        <div v-if="!sidebarCollapsed" class="min-w-0 flex-1">
          <div class="truncate text-sm font-semibold text-gray-900">{{ headerTitle }}</div>
          <div class="truncate text-xs text-gray-500">{{ headerSubtitle }}</div>
        </div>
        <button
          type="button"
          class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-800"
          :title="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          :aria-label="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
          data-testid="sidebar-toggle"
          @click="toggleSidebar"
        >
          <span aria-hidden="true" class="text-base leading-none">{{ sidebarCollapsed ? "»" : "«" }}</span>
        </button>
      </div>
      <!-- Working-entity context. Persistent and always visible inside the module:
           every generative action is scoped to it, and acting on the wrong client is
           the mistake this whole category exists to prevent. -->
      <!-- One level above the entity: which *tenant* am I acting in. Rendered before
           the switcher so a delegated visit is the first thing read, not the last. -->
      <DelegatedBanner v-if="!sidebarCollapsed && currentModule === 'secretarial'" />
      <EntitySwitcher v-if="!sidebarCollapsed && currentModule === 'secretarial'" />
      <nav class="sidebar-scroll flex-1 overflow-y-scroll scroll-smooth p-2">
        <div v-for="(group, gi) in sidebarGroups" :key="gi" class="mb-2">
          <div
            v-if="group.title && !sidebarCollapsed"
            class="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-gray-400"
          >
            {{ group.title }}
          </div>
          <RouterLink
            v-for="item in group.items"
            :key="item.to"
            :to="item.to"
            class="flex items-center rounded-md text-sm font-medium"
            :class="[
              sidebarCollapsed ? 'justify-center px-2 py-2' : 'gap-3 px-3 py-1.5',
              isActive(item.to)
                ? 'bg-primary/10 text-primary'
                : 'text-gray-600 hover:bg-gray-100',
            ]"
            :title="sidebarCollapsed ? item.label : undefined"
          >
            <span v-if="item.icon" aria-hidden="true" class="shrink-0">{{ item.icon }}</span>
            <span v-if="!sidebarCollapsed" class="truncate">{{ item.label }}</span>
          </RouterLink>
        </div>
      </nav>
      <div class="border-t border-gray-200" :class="sidebarCollapsed ? 'p-2' : 'p-4'">
        <template v-if="!sidebarCollapsed">
          <div class="truncate text-sm font-medium text-gray-900">{{ auth.fullName }}</div>
          <div class="truncate text-xs text-gray-500">{{ auth.email }}</div>
          <button class="mt-2 text-xs font-medium text-primary hover:underline" @click="logout">
            Sign out
          </button>
        </template>
        <button
          v-else
          type="button"
          class="mx-auto block text-xs font-medium text-primary hover:underline"
          title="Sign out"
          @click="logout"
        >
          Out
        </button>
      </div>
    </aside>
    <!-- min-w-0: flex children default to min-width:auto and can clip the tax
         workspace's live-result column (seen in Brave / narrower viewports). -->
    <main class="min-w-0 flex-1 overflow-y-auto p-6">
      <!-- Keyed by path, not fullPath: reusing a component instance across e.g.
           /quotations/:id -> /sales-orders/new would keep stale state, and the path
           already changes in those cases. Including the query string here would
           remount the view on every filter, tab or section change, discarding
           unsaved editor state — so views that drive their own query must watch it. -->
      <!-- Keyed on the tenant as well as the path. Switching company replaces every
           record on screen, and a view that stays mounted keeps rendering the previous
           tenant's data until something happens to refetch it — which, on a page whose
           whole job is "whose company am I looking at", is the one stale state that is
           genuinely unsafe. -->
      <p v-if="secretarial.switchingTenant" class="p-6 text-sm text-gray-500">Switching company…</p>
      <RouterView v-else :key="`${auth.companyId ?? 'none'}:${route.path}`" />
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
