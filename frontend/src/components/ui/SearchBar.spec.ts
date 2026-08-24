import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import SearchBar from "./SearchBar.vue";

describe("SearchBar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits update:modelValue after the debounce window", async () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounce: 300 },
    });

    const input = wrapper.find("input");
    await input.setValue("hello");
    await nextTick();

    expect(wrapper.emitted("update:modelValue")).toBeFalsy();
    vi.advanceTimersByTime(300);

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["hello"]);
    wrapper.unmount();
  });

  it("emits search immediately on Enter", async () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounce: 300 },
    });

    const input = wrapper.find("input");
    await input.setValue("now");
    await input.trigger("keydown", { key: "Enter" });
    await nextTick();

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["now"]);
    expect(wrapper.emitted("search")?.[0]).toEqual(["now"]);
    wrapper.unmount();
  });

  it("clears the value and emits an empty update", async () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounce: 300 },
    });

    const input = wrapper.find("input");
    await input.setValue("clear me");
    await nextTick();

    const clearButton = wrapper.find(".search-bar__clear");
    await clearButton.trigger("click");
    await nextTick();

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([""]);
    expect(input.element.value).toBe("");
    wrapper.unmount();
  });
});
