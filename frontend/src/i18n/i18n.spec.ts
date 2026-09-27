import { describe, it, expect, afterEach } from "vitest";
import {
  i18n,
  initializeI18n,
  loadLocale,
  applyLocale,
  detectBrowserLocale,
  resolveLocale,
  formatDateTime,
} from "./index";

function stubBrowserLanguages(languages: string[]) {
  // ``language``/``languages`` are prototype getters in jsdom — shadow
  // them with configurable own properties that afterEach deletes.
  Object.defineProperty(navigator, "languages", {
    value: languages,
    configurable: true,
  });
  Object.defineProperty(navigator, "language", {
    value: languages[0] ?? "en",
    configurable: true,
  });
}

afterEach(() => {
  delete (navigator as { languages?: readonly string[] }).languages;
  delete (navigator as { language?: string }).language;
  localStorage.clear();
  i18n.global.locale.value = "en";
  document.documentElement.removeAttribute("lang");
  document.documentElement.removeAttribute("dir");
});

describe("i18n", () => {
  it("loads en by default", () => {
    expect(i18n.global.locale.value).toBe("en");
    expect(i18n.global.t("common.save")).toBe("Save");
    expect(i18n.global.t("browse.entities.artists")).toBe("Artists");
    expect(i18n.global.t("player.shuffle")).toBe("Shuffle");
  });

  it("restores a stored, supported locale on init", async () => {
    localStorage.setItem("songhive.locale", "en");
    await initializeI18n();
    expect(i18n.global.locale.value).toBe("en");
    expect(i18n.global.t("common.save")).toBe("Save");
  });

  it("falls back to en when the stored locale is unsupported", async () => {
    localStorage.setItem("songhive.locale", "klingon");
    await initializeI18n();
    expect(i18n.global.locale.value).toBe("en");
  });

  it("detects the browser locale when nothing is stored", async () => {
    stubBrowserLanguages(["it-IT", "en"]);
    await initializeI18n();
    expect(i18n.global.locale.value).toBe("it");
    expect(i18n.global.t("common.save")).toBe("Salva");
  });

  it("falls back to en when the browser locale is unsupported", async () => {
    stubBrowserLanguages(["tlh", "fr-FR"]);
    await initializeI18n();
    expect(i18n.global.locale.value).toBe("en");
  });

  it("lets the stored locale override the browser preference", async () => {
    stubBrowserLanguages(["it-IT"]);
    localStorage.setItem("songhive.locale", "en");
    await initializeI18n();
    expect(i18n.global.locale.value).toBe("en");
  });

  it("resolveLocale matches full tags and base languages", () => {
    expect(resolveLocale("it-IT")).toBe("it");
    expect(resolveLocale("IT")).toBe("it");
    expect(resolveLocale("en-US")).toBe("en");
    expect(resolveLocale("fr")).toBeNull();
    expect(resolveLocale(null)).toBeNull();
    expect(resolveLocale("")).toBeNull();
  });

  it("detectBrowserLocale walks the languages list in order", () => {
    stubBrowserLanguages(["fr-FR", "it-IT"]);
    expect(detectBrowserLocale()).toBe("it");
    stubBrowserLanguages(["de-DE"]);
    expect(detectBrowserLocale()).toBe("en");
  });

  it("loadLocale loads Italian messages", async () => {
    expect(await loadLocale("it")).toBe(true);
    i18n.global.locale.value = "it" as "en";
    expect(i18n.global.t("common.save")).toBe("Salva");
    expect(i18n.global.t("browse.entities.artists")).toBe("Artisti");
    expect(i18n.global.t("player.shuffle")).toBe("Casuale");
  });

  it("loadLocale loads Arabic messages", async () => {
    expect(await loadLocale("ar")).toBe(true);
    i18n.global.locale.value = "ar" as "en";
    expect(i18n.global.t("common.save")).toBe("حفظ");
    expect(i18n.global.t("browse.entities.artists")).toBe("الفنانون");
    expect(i18n.global.t("player.shuffle")).toBe("تشغيل عشوائي");
  });

  it("applyLocale sets the document lang and dir", () => {
    applyLocale("it");
    expect(document.documentElement.lang).toBe("it");
    expect(document.documentElement.dir).toBe("ltr");
    applyLocale("ar");
    expect(document.documentElement.lang).toBe("ar");
    expect(document.documentElement.dir).toBe("rtl");
    applyLocale("en");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("detects an RTL browser locale", async () => {
    stubBrowserLanguages(["ar-EG", "en"]);
    await initializeI18n();
    expect(i18n.global.locale.value).toBe("ar");
    expect(document.documentElement.dir).toBe("rtl");
  });

  it("applies Arabic plural categories", async () => {
    await loadLocale("ar");
    i18n.global.locale.value = "ar" as "en";
    const key = "browse.detail.trackCount";
    expect(i18n.global.t(key, 0)).toBe("لا مقاطع");
    expect(i18n.global.t(key, 1)).toBe("مقطع واحد");
    expect(i18n.global.t(key, 2)).toBe("مقطعان");
    expect(i18n.global.t(key, 5)).toBe("5 مقاطع");
    expect(i18n.global.t(key, 15)).toBe("15 مقطع");
  });

  it("loadLocale returns false for unknown locales", async () => {
    expect(await loadLocale("klingon")).toBe(false);
  });

  it("formatDateTime returns a localized string for an ISO date", () => {
    const formatted = formatDateTime("2026-08-24T12:34:56Z", "en-US");
    expect(formatted).toMatch(/Aug 24, 2026/);
    expect(formatted.length).toBeGreaterThan(0);
  });

  it("formatDateTime returns an empty string for null or invalid values", () => {
    expect(formatDateTime(null)).toBe("");
    expect(formatDateTime(undefined)).toBe("");
    expect(formatDateTime("not a date")).toBe("");
  });
});
