import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { i18n } from "@/i18n";
import PageSizeControl from "./PageSizeControl.vue";

function mountControl(props: Record<string, unknown> = {}) {
  return mount(PageSizeControl, {
    props: { modelValue: 20, ...props },
    global: { plugins: [i18n] },
  });
}

describe("PageSizeControl", () => {
  it("selects the current preset size", () => {
    const wrapper = mountControl();
    const select = wrapper.find("select");
    expect((select.element as HTMLSelectElement).value).toBe("20");
    expect(wrapper.find('input[type="number"]').exists()).toBe(false);
  });

  it("emits update:modelValue when a preset is picked", async () => {
    const wrapper = mountControl();
    await wrapper.find("select").setValue("50");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([50]);
  });

  it("reveals the custom input when Custom is picked, without emitting", async () => {
    const wrapper = mountControl();
    await wrapper.find("select").setValue("custom");

    const input = wrapper.find('input[type="number"]');
    expect(input.exists()).toBe(true);
    expect((input.element as HTMLInputElement).value).toBe("20");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("emits the custom value on Enter", async () => {
    const wrapper = mountControl();
    await wrapper.find("select").setValue("custom");
    const input = wrapper.find('input[type="number"]');
    await input.setValue("75");
    await input.trigger("keyup.enter");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([75]);
  });

  it("emits the custom value on blur", async () => {
    const wrapper = mountControl();
    await wrapper.find("select").setValue("custom");
    const input = wrapper.find('input[type="number"]');
    await input.setValue("33");
    await input.trigger("blur");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([33]);
  });

  it("clamps the custom value to the max", async () => {
    const wrapper = mountControl();
    await wrapper.find("select").setValue("custom");
    const input = wrapper.find('input[type="number"]');
    await input.setValue("500");
    await input.trigger("keyup.enter");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([100]);
    expect((input.element as HTMLInputElement).value).toBe("100");
  });

  it("reverts the input on invalid input", async () => {
    const wrapper = mountControl();
    await wrapper.find("select").setValue("custom");
    const input = wrapper.find('input[type="number"]');
    await input.setValue("abc");
    await input.trigger("keyup.enter");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    expect((input.element as HTMLInputElement).value).toBe("20");
  });

  it("starts in custom mode when the model value is not a preset", () => {
    const wrapper = mountControl({ modelValue: 24 });
    expect((wrapper.find("select").element as HTMLSelectElement).value).toBe(
      "custom",
    );
    expect(
      (wrapper.find('input[type="number"]').element as HTMLInputElement).value,
    ).toBe("24");
  });

  it("hides the custom input again when a preset is picked", async () => {
    const wrapper = mountControl({ modelValue: 24 });
    await wrapper.find("select").setValue("50");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([50]);
    expect(wrapper.find('input[type="number"]').exists()).toBe(false);
  });
});
