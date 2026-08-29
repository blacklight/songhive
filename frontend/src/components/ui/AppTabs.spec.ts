import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AppTabs from "./AppTabs.vue";

const tabs = [
  { value: "track", label: "Tracks" },
  { value: "album", label: "Albums" },
];

describe("AppTabs", () => {
  it("renders a tab button for each item", () => {
    const wrapper = mount(AppTabs, {
      props: { modelValue: "track", tabs },
    });

    const buttons = wrapper.findAll("button");
    expect(buttons.length).toBe(2);
    expect(buttons[0].text()).toBe("Tracks");
    expect(buttons[1].text()).toBe("Albums");
  });

  it("marks the active tab with an active class", () => {
    const wrapper = mount(AppTabs, {
      props: { modelValue: "album", tabs },
    });

    const buttons = wrapper.findAll("button");
    expect(buttons[1].classes()).toContain("app-tabs__tab--active");
    expect(buttons[0].classes()).not.toContain("app-tabs__tab--active");
  });

  it("emits update:modelValue when a different tab is clicked", async () => {
    const wrapper = mount(AppTabs, {
      props: { modelValue: "track", tabs },
    });

    await wrapper.findAll("button")[1].trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([["album"]]);
  });

  it("does not emit when the active tab is clicked", async () => {
    const wrapper = mount(AppTabs, {
      props: { modelValue: "track", tabs },
    });

    await wrapper.findAll("button")[0].trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });
});
