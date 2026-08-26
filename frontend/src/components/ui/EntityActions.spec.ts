import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import EntityActions from "./EntityActions.vue";

function makeRect(width: number, height: number): DOMRect {
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => undefined,
  } as DOMRect;
}

function makeActions() {
  return [
    {
      key: "share",
      label: "Share",
      icon: "share-nodes",
      variant: "secondary" as const,
      visible: true,
    },
    {
      key: "add-to-library",
      label: "Add to library",
      icon: "folder-plus",
      visible: true,
    },
    {
      key: "delete",
      label: "Delete",
      icon: "trash",
      variant: "danger" as const,
      visible: true,
    },
  ];
}

describe("EntityActions", () => {
  let wrapper: VueWrapper | null = null;
  let boundingRectSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    boundingRectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue(makeRect(100, 40));
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    document.body.innerHTML = "";
    boundingRectSpy.mockRestore();
  });

  it("renders visible actions as inline buttons and emits select", async () => {
    wrapper = mount(EntityActions, {
      props: {
        actions: makeActions(),
        primaryCount: 3,
      },
      attachTo: document.body,
    });

    const buttons = wrapper.findAll(".entity-actions__item");
    expect(buttons.length).toBe(3);
    expect(buttons[0]!.text()).toContain("Share");

    await buttons[0]!.trigger("click");
    expect(wrapper.emitted("select")?.[0]).toEqual(["share"]);
  });

  it("hides actions marked as not visible", () => {
    wrapper = mount(EntityActions, {
      props: {
        actions: [
          ...makeActions(),
          {
            key: "hidden",
            label: "Hidden",
            visible: false,
          },
        ],
        primaryCount: 3,
      },
      attachTo: document.body,
    });

    expect(wrapper.text()).not.toContain("Hidden");
  });

  it("collapses overflow actions under a More menu", async () => {
    wrapper = mount(EntityActions, {
      props: {
        actions: makeActions(),
        primaryCount: 1,
      },
      attachTo: document.body,
    });

    const primary = wrapper.findAll(".entity-actions__item--primary");
    expect(primary.length).toBe(1);
    expect(primary[0]!.text()).toContain("Share");

    const more = wrapper.find(".entity-actions__more");
    expect(more.exists()).toBe(true);

    await more.trigger("click");
    await flushPromises();

    const menuItems = document.body.querySelectorAll('[role="menuitem"]');
    expect(menuItems.length).toBe(2);
    expect(menuItems[0]!.textContent).toContain("Add to library");
    expect(menuItems[1]!.textContent).toContain("Delete");

    await menuItems[1]!.dispatchEvent(new MouseEvent("click"));
    expect(wrapper.emitted("select")?.[0]).toEqual(["delete"]);
  });

  it("does not render a More button when all actions fit", () => {
    wrapper = mount(EntityActions, {
      props: {
        actions: makeActions().slice(0, 2),
        primaryCount: 2,
      },
      attachTo: document.body,
    });

    expect(wrapper.find(".entity-actions__more").exists()).toBe(false);
    expect(wrapper.findAll(".entity-actions__item").length).toBe(2);
  });
});
