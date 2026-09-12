import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useThemeStore } from "@/stores/theme";
import InterfaceTab from "./InterfaceTab.vue";

describe("InterfaceTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
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
});
