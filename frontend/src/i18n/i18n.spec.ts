import { describe, it, expect } from "vitest";
import { i18n, initializeI18n } from "./index";

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
});
