import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import AppModal from "./AppModal.vue";

describe("AppModal", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

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
    wrapper.unmount();
  });

  it("gives later-opened modals a higher z-index depth", () => {
    const first = mount(AppModal, {
      props: { open: true, title: "First" },
      attachTo: document.body,
    });
    const second = mount(AppModal, {
      props: { open: true, title: "Second" },
      attachTo: document.body,
    });

    const overlays = document.body.querySelectorAll(".app-modal__overlay");
    expect(overlays.length).toBe(2);

    const firstStyle = (overlays[0] as HTMLElement).getAttribute("style");
    const secondStyle = (overlays[1] as HTMLElement).getAttribute("style");

    expect(firstStyle).toContain("--app-modal-depth: 0");
    expect(secondStyle).toContain("--app-modal-depth: 1");
    expect(firstStyle).toContain("var(--z-modal-step)");

    first.unmount();
    second.unmount();
  });
});
