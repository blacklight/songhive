import { computed, ref, watch, type ComputedRef, type Ref } from "vue";
import { defineStore } from "pinia";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_MODE_KEY = "songhive.theme.mode";
const STORAGE_ACCENT_KEY = "songhive.theme.accent";

// Preset accents offered in the interface settings. All values are light
// pastels: --color-accent-contrast (the text/icon color drawn on accent fills)
// is a fixed dark tone in both themes, and the accent must also stay visible
// against the dark theme's gray surfaces.
export const ACCENT_PRESETS: ReadonlyArray<{ value: string; name: string }> = [
  { value: "#fca5a5", name: "red" },
  { value: "#fdba74", name: "orange" },
  { value: "#ecd34d", name: "yellow" },
  { value: "#b4b465", name: "olive" },
  { value: "#86efac", name: "green" },
  { value: "#4ef0d8", name: "turquoise" },
  { value: "#67e8f9", name: "cyan" },
  { value: "#43c5fd", name: "blue" },
  { value: "#d8b4fe", name: "purple" },
  { value: "#acb3bf", name: "gray" },
];

const DEFAULT_ACCENT = ACCENT_PRESETS.filter(
  (preset) => preset.name === "yellow",
)[0].value;

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
