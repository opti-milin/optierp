// Per-company module feature flags. Default: manufacturing + taxation on.
import { defineStore } from "pinia";
import { api } from "@/api/client";

export interface ModuleFlags {
  manufacturing: boolean;
  taxation: boolean;
}

export const useModuleFlagsStore = defineStore("moduleFlags", {
  state: (): { flags: ModuleFlags; loaded: boolean } => ({
    flags: { manufacturing: true, taxation: true },
    loaded: false,
  }),
  actions: {
    async load(): Promise<void> {
      try {
        this.flags = (await api.get<ModuleFlags>("/settings/module-flags")).data;
      } catch {
        this.flags = { manufacturing: true, taxation: true };
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
  },
});
