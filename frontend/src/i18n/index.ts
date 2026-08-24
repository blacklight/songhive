import { createI18n } from "vue-i18n";
import en from "./locales/en.json";

const STORAGE_LOCALE_KEY = "songhive.locale";

const localeLoaders: Record<string, () => Promise<Record<string, unknown>>> = {
  en: () => Promise.resolve(en),
};

export function getSupportedLocales(): string[] {
  return Object.keys(localeLoaders);
}

export function getStoredLocale(): string {
  return localStorage.getItem(STORAGE_LOCALE_KEY) || "en";
}

export function setStoredLocale(locale: string) {
  localStorage.setItem(STORAGE_LOCALE_KEY, locale);
}

export const i18n = createI18n({
  legacy: false,
  locale: "en",
  fallbackLocale: "en",
  messages: { en },
});

const loadedLocales = new Set<string>(["en"]);

export async function loadLocale(locale: string): Promise<boolean> {
  if (loadedLocales.has(locale)) return true;
  const loader = localeLoaders[locale];
  if (!loader) return false;
  try {
    const messages = await loader();
    i18n.global.setLocaleMessage(locale, messages as never);
    loadedLocales.add(locale);
    return true;
  } catch {
    return false;
  }
}

export async function initializeI18n(): Promise<void> {
  const stored = getStoredLocale();
  const initial = getSupportedLocales().includes(stored) ? stored : "en";
  if (initial !== "en") {
    await loadLocale(initial);
  }
  i18n.global.locale.value = initial as "en";
}

export function formatDateTime(
  value: string | Date | number | null | undefined,
  locale?: string,
): string {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(locale || (i18n.global.locale.value as string), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
