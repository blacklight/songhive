import { computed } from "vue";
import { useThemeStore } from "@/stores/theme";

export function useTheme() {
  const store = useThemeStore();

  const resolvedMode = computed(() => store.resolvedMode);

  function toggle() {
    const current = resolvedMode.value;
    store.setMode(current === "dark" ? "light" : "dark");
  }

  return {
    mode: computed(() => store.mode),
    accent: computed(() => store.accent),
    resolvedMode,
    setMode: store.setMode,
    setAccent: store.setAccent,
    toggle,
  };
}
