import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { ACCENT_PRESETS, useThemeStore } from "@/stores/theme";
import InterfaceTab from "./InterfaceTab.vue";

describe("InterfaceTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.removeProperty("--accent");
  });

  it("renders the theme selector with the current mode", () => {
    const wrapper = mount(InterfaceTab);
    const select = wrapper.find("select");

    expect(select.exists()).toBe(true);
    expect((select.element as HTMLSelectElement).value).toBe("system");
    expect(wrapper.findAll("option").map((o) => o.text())).toEqual([
      i18n.global.t("theme.system"),
      i18n.global.t("theme.light"),
      i18n.global.t("theme.dark"),
    ]);
  });

  it("persists the selected theme through the theme store", async () => {
    const wrapper = mount(InterfaceTab);
    await wrapper.find("select").setValue("dark");

    const store = useThemeStore();
    expect(store.mode).toBe("dark");
    expect(localStorage.getItem("songhive.theme.mode")).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("renders a swatch per accent preset with the current one checked", () => {
    const wrapper = mount(InterfaceTab);
    const swatches = wrapper.findAll('.interface-tab__swatch[role="radio"]');

    expect(swatches).toHaveLength(ACCENT_PRESETS.length);
    const checked = swatches.filter(
      (s) => s.attributes("aria-checked") === "true",
    );
    expect(checked).toHaveLength(1);
    expect(checked[0].attributes("aria-label")).toBe(
      i18n.global.t("theme.accents.yellow"),
    );
  });

  it("persists the selected accent through the theme store", async () => {
    const wrapper = mount(InterfaceTab);
    const blue = ACCENT_PRESETS.find((p) => p.name === "blue")!;
    const swatch = wrapper
      .findAll('.interface-tab__swatch[role="radio"]')
      .find(
        (s) =>
          s.attributes("aria-label") === i18n.global.t("theme.accents.blue"),
      );
    expect(swatch).toBeDefined();

    await swatch!.trigger("click");

    const store = useThemeStore();
    expect(store.accent).toBe(blue.value);
    expect(localStorage.getItem("songhive.theme.accent")).toBe(blue.value);
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe(
      blue.value,
    );
    expect(swatch!.attributes("aria-checked")).toBe("true");
  });
});
