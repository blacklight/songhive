import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useThemeStore } from "./theme";

describe("useThemeStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.removeProperty("--accent");
    localStorage.clear();
  });

  it("setMode persists and applies dark", () => {
    const store = useThemeStore();
    store.setMode("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("songhive.theme.mode")).toBe("dark");
  });

  it("setAccent persists and applies the source token", () => {
    const store = useThemeStore();
    store.setAccent("#ff0000");
    expect(localStorage.getItem("songhive.theme.accent")).toBe("#ff0000");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe(
      "#ff0000",
    );
  });
});
