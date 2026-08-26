import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AppInput from "./AppInput.vue";

describe("AppInput", () => {
  it("sets aria-invalid and renders error", () => {
    const wrapper = mount(AppInput, {
      props: { modelValue: "", error: "bad" },
    });
    const input = wrapper.find("input");
    expect(input.attributes("aria-invalid")).toBe("true");
    expect(wrapper.text()).toContain("bad");
  });

  it("renders a textarea when as is 'textarea'", () => {
    const wrapper = mount(AppInput, {
      props: { modelValue: "hello", as: "textarea" },
    });
    const textarea = wrapper.find("textarea");
    expect(textarea.exists()).toBe(true);
    expect((textarea.element as HTMLTextAreaElement).value).toBe("hello");
  });
});
