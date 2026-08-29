import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import StatCard from "./StatCard.vue";

describe("StatCard", () => {
  it("renders label, value, and icon", () => {
    const wrapper = mount(StatCard, {
      props: { label: "Files", value: 42, icon: "file" },
    });
    expect(wrapper.text()).toContain("Files");
    expect(wrapper.text()).toContain("42");
    expect(wrapper.find("i").exists()).toBe(true);
  });

  it("hides icon when omitted", () => {
    const wrapper = mount(StatCard, {
      props: { label: "Files", value: 42 },
    });
    expect(wrapper.find("i").exists()).toBe(false);
  });

  it("shows a skeleton while loading", () => {
    const wrapper = mount(StatCard, {
      props: { label: "Files", value: 42, loading: true },
    });
    expect(wrapper.find(".skeleton").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("42");
  });
});
