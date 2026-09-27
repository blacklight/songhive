import { computed, ref } from "vue";
import {
  i18n,
  applyLocale,
  loadLocale,
  setStoredLocale,
  clearStoredLocale,
  getStoredLocale,
  getSupportedLocales,
  detectBrowserLocale,
} from "@/i18n";

export function useLocale() {
  const locale = computed(() => i18n.global.locale.value);
  const available = getSupportedLocales();
  const stored = ref<string | null>(getStoredLocale());

  async function setLocale(value: string | null) {
    const target = value || detectBrowserLocale();
    const ok = await loadLocale(target);
    if (!ok) return;
    applyLocale(target);
    if (value) {
      setStoredLocale(value);
    } else {
      clearStoredLocale();
    }
    stored.value = value || null;
  }

  return { locale, available, stored, setLocale };
}
