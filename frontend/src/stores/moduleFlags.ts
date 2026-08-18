// Per-company module feature flags. Manufacturing + taxation default on;
// secretarial is opt-in (Module 13).
import { defineStore } from "pinia";
import { api } from "@/api/client";

export interface ModuleFlags {
  manufacturing: boolean;
  taxation: boolean;
  secretarial: boolean;
}

export const useModuleFlagsStore = defineStore("moduleFlags", {
  state: (): { flags: ModuleFlags; loaded: boolean } => ({
    flags: { manufacturing: true, taxation: true, secretarial: false },
    loaded: false,
  }),
  actions: {
    async load(): Promise<void> {
      try {
        this.flags = (await api.get<ModuleFlags>("/settings/module-flags")).data;
      } catch {
        this.flags = { manufacturing: true, taxation: true, secretarial: false };
      } finally {
        this.loaded = true;
      }
    },
    async setManufacturing(enabled: boolean): Promise<void> {
      this.flags = (
        await api.put<ModuleFlags>("/settings/module-flags", { manufacturing: enabled })
      ).data;
    },
    async setTaxation(enabled: boolean): Promise<void> {
      this.flags = (
        await api.put<ModuleFlags>("/settings/module-flags", { taxation: enabled })
      ).data;
    },
    async setSecretarial(enabled: boolean): Promise<void> {
      this.flags = (
        await api.put<ModuleFlags>("/settings/module-flags", { secretarial: enabled })
      ).data;
    },
  },
});
