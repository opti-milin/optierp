// Working-entity context for the Secretarial module.
//
// Every generative action in this module is scoped to one entity, and picking the
// wrong client is the category's classic mistake — so the selection is explicit,
// persisted, and shown in the sidebar at all times rather than inferred per page.
import { defineStore } from "pinia";
import { api } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import type { ListResponse } from "@/types/core";
import type { SecretarialEntity, SecretarialSettings } from "@/types/secretarial";

const STORAGE_KEY = "secretarial.entityId";
// Where "back to my own practice" goes. Persisted because a delegated visit outlives
// a page load, and a CS stranded inside a client's tenant with no way home is the
// worst failure this flow can have.
const HOME_KEY = "secretarial.practiceHome";

interface PracticeHome {
  companyId: string;
  name: string;
}

interface State {
  settings: SecretarialSettings | null;
  entities: SecretarialEntity[];
  entityId: string | null;
  loaded: boolean;
  loading: boolean;
  /** Set only while this session is acting inside a delegated client's tenant. */
  practiceHome: PracticeHome | null;
  /**
   * True while a company switch is in flight.
   *
   * The shell unmounts the routed view for the duration. Without that, a view mounted
   * under the old tenant keeps its `onMounted` chain running across the switch and
   * fires its remaining requests with the new token — which is both a 403 in the
   * console and, more to the point, a request for one tenant's data made while
   * authenticated as another.
   */
  switchingTenant: boolean;
}

function readHome(): PracticeHome | null {
  try {
    const raw = localStorage.getItem(HOME_KEY);
    return raw ? (JSON.parse(raw) as PracticeHome) : null;
  } catch {
    return null;
  }
}

export const useSecretarialStore = defineStore("secretarial", {
  state: (): State => ({
    settings: null,
    entities: [],
    entityId: localStorage.getItem(STORAGE_KEY),
    loaded: false,
    loading: false,
    practiceHome: readHome(),
    switchingTenant: false,
  }),

  getters: {
    entity: (state): SecretarialEntity | null =>
      state.entities.find((e) => e.id === state.entityId) ?? null,
    isPractice: (state): boolean => state.settings?.profile === "practice",
    // A practice needs the switcher as primary navigation; a business tenant has
    // exactly one entity and should never see the concept at all.
    showSwitcher: (state): boolean =>
      state.settings?.profile === "practice" || state.entities.length > 1,
  },

  actions: {
    /** Idempotent: prepares the module, then loads settings and entities. */
    async load(force = false): Promise<void> {
      if (this.loaded && !force) return;
      this.loading = true;
      try {
        // A hard reload re-issues the token against the user's default company, which
        // silently ends a delegated visit. Settle that before anything reads the flag.
        this.reconcileDelegated(useAuthStore().companyId);
        this.settings = (await api.post<SecretarialSettings>("/secretarial/bootstrap")).data;
        await this.refreshEntities();
        this.loaded = true;
      } finally {
        this.loading = false;
      }
    },

    async refreshEntities(): Promise<void> {
      const resp = await api.get<ListResponse<SecretarialEntity>>("/secretarial/entities", {
        params: { page_size: 200 },
      });
      this.entities = resp.data.items;
      // Fall back to the first entity if the remembered one is gone (revoked
      // engagement, deleted client) — never leave the module pointing at nothing.
      if (!this.entityId || !this.entities.some((e) => e.id === this.entityId)) {
        this.setEntity(this.entities[0]?.id ?? null);
      }
    },

    setEntity(id: string | null): void {
      this.entityId = id;
      if (id) localStorage.setItem(STORAGE_KEY, id);
      else localStorage.removeItem(STORAGE_KEY);
    },

    async updateSettings(patch: Partial<SecretarialSettings>): Promise<void> {
      this.settings = (await api.patch<SecretarialSettings>("/secretarial/settings", patch)).data;
    },

    /** Step into a delegated client's own tenant, remembering the way back. */
    async enterDelegated(
      client: { owner_company_id: string; entity_id: string; entity_name: string },
      firm: { companyId: string; name: string },
      navigate?: () => Promise<unknown>,
    ): Promise<void> {
      const auth = useAuthStore();
      const home: PracticeHome = this.practiceHome ?? firm;

      // The guard goes up first and comes down last, with the route change inside it.
      // Both ends matter: leaving a firm-side view mounted during the switch lets its
      // in-flight requests land under the new token, and dropping the guard before
      // navigating lets that same view remount under the client tenant and refetch.
      // Either way the symptom is a 403 for data this session should never ask for.
      this.switchingTenant = true;
      try {
        await navigate?.();
        await auth.switchCompany(client.owner_company_id);
        // The token now carries the client's tenant, so settings and entities have to
        // be re-read: the previous ones belong to the firm and would silently mislabel
        // every screen in the module.
        this.practiceHome = home;
        localStorage.setItem(HOME_KEY, JSON.stringify(home));
        await this.load(true);
        this.setEntity(client.entity_id);
      } finally {
        this.switchingTenant = false;
      }
    },

    /** Return to the practice's own tenant. */
    async leaveDelegated(): Promise<void> {
      if (!this.practiceHome) return;
      const auth = useAuthStore();
      this.switchingTenant = true;
      try {
        await auth.switchCompany(this.practiceHome.companyId);
        this.clearDelegated();
        await this.load(true);
      } finally {
        this.switchingTenant = false;
      }
    },

    clearDelegated(): void {
      this.practiceHome = null;
      localStorage.removeItem(HOME_KEY);
    },

    /**
     * Reconcile the remembered visit against the tenant the token actually carries.
     *
     * `POST /auth/refresh` re-issues against `users.default_company_id`, so a hard
     * reload drops the session back to the firm while the remembered visit is still on
     * disk. Without this the banner would claim we are inside a client we have already
     * left — worse than showing nothing, because it is the indicator people trust.
     */
    reconcileDelegated(currentCompanyId: string | null): void {
      if (this.practiceHome && currentCompanyId === this.practiceHome.companyId) {
        this.clearDelegated();
      }
    },
  },
});
