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

  it("renders title and uses it as the default aria-label", () => {
    const wrapper = mount(AppButton, { props: { title: "Save" } });
    const button = wrapper.find("button");
    expect(button.attributes("title")).toBe("Save");
    expect(button.attributes("aria-label")).toBe("Save");
  });

  it("prefers ariaLabel over title for the accessible name", () => {
    const wrapper = mount(AppButton, {
      props: { title: "Save", ariaLabel: "Submit" },
    });
    const button = wrapper.find("button");
    expect(button.attributes("title")).toBe("Save");
    expect(button.attributes("aria-label")).toBe("Submit");
  });

  it("does not add title or aria-label when neither prop is provided", () => {
    const wrapper = mount(AppButton);
    const button = wrapper.find("button");
    expect(button.attributes("title")).toBeUndefined();
    expect(button.attributes("aria-label")).toBeUndefined();
  });
});
