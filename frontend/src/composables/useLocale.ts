import { computed } from "vue";
import {
  i18n,
  loadLocale,
  setStoredLocale,
  getStoredLocale,
  getSupportedLocales,
} from "@/i18n";

export function useLocale() {
  const locale = computed(() => i18n.global.locale.value);
  const available = getSupportedLocales();

  async function setLocale(value: string) {
    const ok = await loadLocale(value);
    if (!ok) return;
    i18n.global.locale.value = value as "en";
    setStoredLocale(value);
  }

  return { locale, available, setLocale, restore: getStoredLocale };
}
