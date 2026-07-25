// Per-company module feature flags (Phase 6). Default: manufacturing on.
import { defineStore } from "pinia";
import { api } from "@/api/client";

export interface ModuleFlags {
  manufacturing: boolean;
}

export const useModuleFlagsStore = defineStore("moduleFlags", {
  state: (): { flags: ModuleFlags; loaded: boolean } => ({
    flags: { manufacturing: true },
    loaded: false,
  }),
  actions: {
    async load(): Promise<void> {
      try {
        this.flags = (await api.get<ModuleFlags>("/settings/module-flags")).data;
      } catch {
        this.flags = { manufacturing: true };
      } finally {
        this.loaded = true;
      }
    },
    async setManufacturing(enabled: boolean): Promise<void> {
      this.flags = (
        await api.put<ModuleFlags>("/settings/module-flags", { manufacturing: enabled })
      ).data;
    },
  },
});
