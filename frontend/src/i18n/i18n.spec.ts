import { describe, it, expect } from "vitest";
import { i18n, initializeI18n, formatDateTime } from "./index";

describe("i18n", () => {
  it("loads en by default", () => {
    expect(i18n.global.locale.value).toBe("en");
    expect(i18n.global.t("common.save")).toBe("Save");
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
