import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import { defineStore } from "pinia";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_MODE_KEY = "songhive.theme.mode";
const STORAGE_ACCENT_KEY = "songhive.theme.accent";
const DEFAULT_ACCENT = "#fcd34d";

function readStoredMode(): ThemeMode {
  const raw = localStorage.getItem(STORAGE_MODE_KEY);
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

function readStoredAccent(): string {
  return localStorage.getItem(STORAGE_ACCENT_KEY) || DEFAULT_ACCENT;
}

export const useThemeStore = defineStore("theme", () => {
  const mode: Ref<ThemeMode> = ref(readStoredMode());
  const accent: Ref<string> = ref(readStoredAccent());

  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const systemDark: Ref<boolean> = ref(mediaQuery.matches);

  const onSystemChange = (event: MediaQueryListEvent) => {
    systemDark.value = event.matches;
    apply();
  };
  mediaQuery.addEventListener("change", onSystemChange);

  const resolvedMode: ComputedRef<"light" | "dark"> = computed(() => {
    if (mode.value === "system") {
      return systemDark.value ? "dark" : "light";
    }
    return mode.value;
  });

  function setMode(value: ThemeMode) {
    mode.value = value;
    localStorage.setItem(STORAGE_MODE_KEY, value);
    apply();
  }

  function setAccent(value: string) {
    accent.value = value;
    localStorage.setItem(STORAGE_ACCENT_KEY, value);
    apply();
  }

  function apply() {
    document.documentElement.setAttribute("data-theme", resolvedMode.value);
    document.documentElement.style.setProperty("--accent", accent.value);
  }

  function dispose() {
    mediaQuery.removeEventListener("change", onSystemChange);
  }

  watch(resolvedMode, apply);
  watch(accent, apply);

  return { mode, accent, resolvedMode, setMode, setAccent, apply, dispose };
});
