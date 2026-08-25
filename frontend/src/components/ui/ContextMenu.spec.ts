import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import ContextMenu from "./ContextMenu.vue";

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

describe("ContextMenu", () => {
  let innerWidthSpy: ReturnType<typeof vi.spyOn>;
  let innerHeightSpy: ReturnType<typeof vi.spyOn>;
  let boundingRectSpy: ReturnType<typeof vi.spyOn>;
  let wrapper: VueWrapper | null = null;

  beforeEach(() => {
    innerWidthSpy = vi.spyOn(window, "innerWidth", "get").mockReturnValue(800);
    innerHeightSpy = vi
      .spyOn(window, "innerHeight", "get")
      .mockReturnValue(600);
    boundingRectSpy = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue(makeRect(160, 200));
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    document.body.innerHTML = "";
    innerWidthSpy.mockRestore();
    innerHeightSpy.mockRestore();
    boundingRectSpy.mockRestore();
  });

  it("emits select on item click", async () => {
    wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 0,
        y: 0,
      },
      attachTo: document.body,
    });
    await flushPromises();

    const item = document.body.querySelector('[role="menuitem"]');
    expect(item).toBeTruthy();
    if (item) {
      await item.dispatchEvent(new MouseEvent("click"));
      expect(wrapper.emitted("select")).toBeTruthy();
      expect(wrapper.emitted("select")?.[0]).toEqual(["edit"]);
    }
  });

  it("positions at x,y when the menu fits in the viewport", async () => {
    wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 100,
        y: 100,
      },
      attachTo: document.body,
    });
    await flushPromises();

    const menu = document.body.querySelector(".context-menu") as HTMLElement;
    expect(menu.style.left).toBe("100px");
    expect(menu.style.top).toBe("100px");
    expect(menu.style.visibility).toBe("visible");
  });

  it("flips to the left when the menu would overflow the right edge", async () => {
    innerWidthSpy.mockReturnValue(400);
    innerHeightSpy.mockReturnValue(600);

    wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 300,
        y: 100,
      },
      attachTo: document.body,
    });
    await flushPromises();

    const menu = document.body.querySelector(".context-menu") as HTMLElement;
    expect(menu.style.left).toBe("140px"); // 300 - 160
    expect(menu.style.top).toBe("100px");
  });

  it("flips to the top when the menu would overflow the bottom edge", async () => {
    innerWidthSpy.mockReturnValue(800);
    innerHeightSpy.mockReturnValue(600);

    wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 100,
        y: 450,
      },
      attachTo: document.body,
    });
    await flushPromises();

    const menu = document.body.querySelector(".context-menu") as HTMLElement;
    expect(menu.style.left).toBe("100px");
    expect(menu.style.top).toBe("250px"); // 450 - 200
  });

  it("flips both horizontally and vertically when needed", async () => {
    innerWidthSpy.mockReturnValue(400);
    innerHeightSpy.mockReturnValue(600);

    wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 300,
        y: 450,
      },
      attachTo: document.body,
    });
    await flushPromises();

    const menu = document.body.querySelector(".context-menu") as HTMLElement;
    expect(menu.style.left).toBe("140px"); // 300 - 160
    expect(menu.style.top).toBe("250px"); // 450 - 200
  });

  it("clamps the menu to the viewport origin when it cannot fit either side", async () => {
    innerWidthSpy.mockReturnValue(200);
    innerHeightSpy.mockReturnValue(200);

    wrapper = mount(ContextMenu, {
      props: {
        items: [{ key: "edit", label: "Edit" }],
        open: true,
        x: 100,
        y: 100,
      },
      attachTo: document.body,
    });
    await flushPromises();

    const menu = document.body.querySelector(".context-menu") as HTMLElement;
    expect(menu.style.left).toBe("0px");
    expect(menu.style.top).toBe("0px");
  });
});
