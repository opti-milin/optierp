// Working-entity context for the Secretarial module.
//
// Every generative action in this module is scoped to one entity, and picking the
// wrong client is the category's classic mistake — so the selection is explicit,
// persisted, and shown in the sidebar at all times rather than inferred per page.
import { defineStore } from "pinia";
import { api } from "@/api/client";
import type { ListResponse } from "@/types/core";
import type { SecretarialEntity, SecretarialSettings } from "@/types/secretarial";

const STORAGE_KEY = "secretarial.entityId";

interface State {
  settings: SecretarialSettings | null;
  entities: SecretarialEntity[];
  entityId: string | null;
  loaded: boolean;
  loading: boolean;
}

export const useSecretarialStore = defineStore("secretarial", {
  state: (): State => ({
    settings: null,
    entities: [],
    entityId: localStorage.getItem(STORAGE_KEY),
    loaded: false,
    loading: false,
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
  },
});
