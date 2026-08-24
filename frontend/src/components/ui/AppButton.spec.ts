import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AppButton from "./AppButton.vue";

describe("AppButton", () => {
  it("disables and sets aria-busy when loading", () => {
    const wrapper = mount(AppButton, { props: { loading: true } });
    const button = wrapper.find("button");
    expect(button.attributes("disabled")).toBe("");
    expect(button.attributes("aria-busy")).toBe("true");
  });
});
