// Window-level keyboard shortcut registry.
//
// `mod` resolves to the command key on a Mac and the control key elsewhere, so
// one spec covers both. Specs are read through a getter so a component can vary
// them with its own state; the returned `shortcuts` computed drives a help list.
//
// Safe to call from several components at once: each call owns one listener and
// only ever handles a combo it was given.

import { computed, onMounted, onUnmounted, type ComputedRef } from "vue";

export interface ShortcutSpec {
  combo: string;
  description: string;
  handler: (event: KeyboardEvent) => void;
  allowInInput?: boolean;
}

const EDITABLE_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isMacPlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  return /mac|iphone|ipad|ipod/i.test(navigator.platform || navigator.userAgent);
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (EDITABLE_TAGS.has(target.tagName)) return true;
  return target.isContentEditable;
}

function normaliseKey(key: string): string {
  if (key === " ") return "space";
  return key.toLowerCase();
}

function matches(spec: ShortcutSpec, event: KeyboardEvent, mac: boolean): boolean {
  const parts = spec.combo
    .toLowerCase()
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) return false;

  const key = parts[parts.length - 1];
  const wantMod = parts.includes("mod") || parts.includes("ctrl") || parts.includes("meta") || parts.includes("cmd");
  const wantShift = parts.includes("shift");
  const wantAlt = parts.includes("alt") || parts.includes("option");

  const mod = mac ? event.metaKey : event.ctrlKey;
  if (wantMod !== mod) return false;
  if (wantAlt !== event.altKey) return false;

  // A punctuation combo such as "?" is typed with shift on most layouts, so the
  // shift state is only enforced for alphanumeric keys.
  const punctuation = key.length === 1 && !/[a-z0-9]/.test(key);
  if (!punctuation && wantShift !== event.shiftKey) return false;

  return normaliseKey(event.key) === key;
}

export function useKeyboardShortcuts(getSpecs: () => ShortcutSpec[]): {
  shortcuts: ComputedRef<ShortcutSpec[]>;
} {
  const shortcuts = computed<ShortcutSpec[]>(() => getSpecs());
  const mac = isMacPlatform();

  function onKeydown(event: KeyboardEvent): void {
    for (const spec of shortcuts.value) {
      if (!spec.allowInInput && isEditableTarget(event.target)) continue;
      if (!matches(spec, event, mac)) continue;
      event.preventDefault();
      spec.handler(event);
      return;
    }
  }

  onMounted(() => window.addEventListener("keydown", onKeydown));
  onUnmounted(() => window.removeEventListener("keydown", onKeydown));

  return { shortcuts };
}
