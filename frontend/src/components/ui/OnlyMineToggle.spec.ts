import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import OnlyMineToggle from "./OnlyMineToggle.vue";

describe("OnlyMineToggle", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders nothing when signed out", () => {
    const wrapper = mount(OnlyMineToggle, {
      props: { modelValue: false },
    });

    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
  });

  it("renders a labelled checkbox when signed in", () => {
    const authStore = useAuthStore();
    authStore.user = { id: "user-1", username: "alice" } as never;

    const wrapper = mount(OnlyMineToggle, {
      props: { modelValue: false },
    });

    const checkbox = wrapper.find('input[type="checkbox"]');
    expect(checkbox.exists()).toBe(true);
    expect(wrapper.text()).toContain(i18n.global.t("browse.list.onlyMine"));
  });

  it("emits update:modelValue when toggled", async () => {
    const authStore = useAuthStore();
    authStore.user = { id: "user-1", username: "alice" } as never;

    const wrapper = mount(OnlyMineToggle, {
      props: { modelValue: false },
    });

    await wrapper.find('input[type="checkbox"]').setValue(true);
    expect(wrapper.emitted("update:modelValue")).toEqual([[true]]);
  });
});
