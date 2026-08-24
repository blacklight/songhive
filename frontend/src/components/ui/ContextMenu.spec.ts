import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ContextMenu from "./ContextMenu.vue";

describe("ContextMenu", () => {
  it("emits select on item click", async () => {
    const wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 0,
        y: 0,
      },
      attachTo: document.body,
    });
    const item = document.body.querySelector('[role="menuitem"]');
    expect(item).toBeTruthy();
    if (item) {
      await item.dispatchEvent(new MouseEvent("click"));
      expect(wrapper.emitted("select")).toBeTruthy();
      expect(wrapper.emitted("select")?.[0]).toEqual(["edit"]);
    }
  });
});
