import { createI18n } from "vue-i18n";
import en from "./locales/en.json";

const STORAGE_LOCALE_KEY = "songhive.locale";

const localeLoaders: Record<string, () => Promise<Record<string, unknown>>> = {
  en: () => Promise.resolve(en),
  ar: () => import("./locales/ar.json").then((m) => m.default),
  es: () => import("./locales/es.json").then((m) => m.default),
  it: () => import("./locales/it.json").then((m) => m.default),
};

const RTL_LOCALES = new Set([
  "ar",
  "ckb",
  "dv",
  "fa",
  "he",
  "ps",
  "sd",
  "ug",
  "ur",
  "yi",
]);

export function isRtlLocale(locale: string): boolean {
  return RTL_LOCALES.has(locale.toLowerCase().split("-")[0]);
}

// CLDR plural categories for Arabic, in pipe order:
// zero | one | two | few (n%100 in 3-10) | many (n%100 in 11-99) | other.
function arabicPluralRule(choice: number, choicesLength: number): number {
  let index: number;
  if (choice === 0) index = 0;
  else if (choice === 1) index = 1;
  else if (choice === 2) index = 2;
  else {
    const mod100 = choice % 100;
    index = mod100 >= 3 && mod100 <= 10 ? 3 : mod100 >= 11 ? 4 : 5;
  }
  return Math.min(index, choicesLength - 1);
}

export function getSupportedLocales(): string[] {
  return Object.keys(localeLoaders);
}

export function getStoredLocale(): string | null {
  return localStorage.getItem(STORAGE_LOCALE_KEY) || null;
}

export function setStoredLocale(locale: string) {
  localStorage.setItem(STORAGE_LOCALE_KEY, locale);
}

export function clearStoredLocale() {
  localStorage.removeItem(STORAGE_LOCALE_KEY);
}

export function resolveLocale(
  candidate: string | null | undefined,
): string | null {
  if (!candidate) return null;
  const supported = getSupportedLocales();
  const normalized = candidate.toLowerCase();
  if (supported.includes(normalized)) return normalized;
  const base = normalized.split("-")[0];
  return (
    supported.find((locale) => locale.toLowerCase().split("-")[0] === base) ??
    null
  );
}

export function detectBrowserLocale(): string {
  const candidates =
    typeof navigator === "undefined"
      ? []
      : navigator.languages?.length
        ? navigator.languages
        : [navigator.language];
  for (const candidate of candidates) {
    const resolved = resolveLocale(candidate);
    if (resolved) return resolved;
  }
  return "en";
}

export const i18n = createI18n({
  legacy: false,
  locale: "en",
  fallbackLocale: "en",
  messages: { en },
  pluralRules: { ar: arabicPluralRule },
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

export function applyLocale(locale: string) {
  i18n.global.locale.value = locale as "en";
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
    document.documentElement.dir = isRtlLocale(locale) ? "rtl" : "ltr";
  }
}

export async function initializeI18n(): Promise<void> {
  const initial = resolveLocale(getStoredLocale()) ?? detectBrowserLocale();
  const loaded = initial === "en" || (await loadLocale(initial));
  applyLocale(loaded ? initial : "en");
}

export function formatDateTime(
  value: string | Date | number | null | undefined,
  locale?: string,
): string {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(
    locale || (i18n.global.locale.value as string),
    {
      dateStyle: "medium",
      timeStyle: "short",
    },
  ).format(date);
}

export function formatDate(
  value: string | Date | number | null | undefined,
  locale?: string,
): string {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(
    locale || (i18n.global.locale.value as string),
    {
      dateStyle: "medium",
    },
  ).format(date);
}

const RELATIVE_DIVISIONS: {
  amount: number;
  unit: Intl.RelativeTimeFormatUnit;
}[] = [
  { amount: 60, unit: "second" },
  { amount: 60, unit: "minute" },
  { amount: 24, unit: "hour" },
  { amount: 7, unit: "day" },
  { amount: 4.34524, unit: "week" },
  { amount: 12, unit: "month" },
  { amount: Number.POSITIVE_INFINITY, unit: "year" },
];

export function formatRelativeTime(
  value: string | Date | number | null | undefined,
  locale?: string,
): string {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const rtf = new Intl.RelativeTimeFormat(
    locale || (i18n.global.locale.value as string),
    { numeric: "auto" },
  );
  let duration = (date.getTime() - Date.now()) / 1000;
  for (const division of RELATIVE_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return rtf.format(Math.round(duration), division.unit);
    }
    duration /= division.amount;
  }
  return "";
}
