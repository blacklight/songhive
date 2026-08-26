import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AppModal from "./AppModal.vue";

describe("AppModal", () => {
  it("renders and closes on backdrop click", async () => {
    const wrapper = mount(AppModal, {
      props: { open: true, title: "Test" },
      attachTo: document.body,
    });
    const overlay = document.body.querySelector(".app-modal__overlay");
    expect(overlay).toBeTruthy();
    if (overlay) {
      await overlay.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    }
    expect(wrapper.emitted("close")).toBeTruthy();
  });
});
