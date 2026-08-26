import { describe, it, expect } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import DeleteModal from "./DeleteModal.vue";

function mountModal(props: Record<string, unknown> = {}) {
  return mount(DeleteModal, {
    props: {
      open: true,
      title: "Delete item?",
      message: "Are you sure?",
      allowRecursive: false,
      loading: false,
      ...props,
    },
    attachTo: document.body,
  });
}

function findButton(text: string): HTMLButtonElement | null {
  const buttons = document.body.querySelectorAll("button");
  for (const btn of buttons) {
    if (btn.textContent?.trim() === text) return btn;
  }
  return null;
}

describe("DeleteModal", () => {
  it("emits close when cancel is clicked", async () => {
    const wrapper = mountModal();
    await flushPromises();

    const cancel = findButton("Cancel");
    cancel?.click();
    await flushPromises();

    expect(wrapper.emitted("close")?.length).toBe(1);
    wrapper.unmount();
  });

  it("emits confirm without recursive when no checkbox", async () => {
    const wrapper = mountModal({ allowRecursive: false });
    await flushPromises();

    const confirm = findButton("Delete");
    confirm?.click();
    await flushPromises();

    expect(wrapper.emitted("confirm")?.[0]).toEqual([false]);
    wrapper.unmount();
  });

  it("toggles recursive and emits confirm with true", async () => {
    const wrapper = mountModal({
      allowRecursive: true,
      recursiveLabel: "Delete contents",
    });
    await flushPromises();

    const checkbox = document.body.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement | null;
    if (checkbox) {
      checkbox.checked = true;
      checkbox.dispatchEvent(new Event("change"));
    }
    await flushPromises();

    const confirm = findButton("Delete");
    confirm?.click();
    await flushPromises();

    expect(wrapper.emitted("confirm")?.[0]).toEqual([true]);
    wrapper.unmount();
  });
});
