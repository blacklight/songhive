import { describe, it, expect, vi } from "vitest";
import { mount, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import { i18n } from "@/i18n";
import AlphabetIndex from "./AlphabetIndex.vue";
import { INDEX_LETTERS } from "@/utils/indexLetters";

const ROW_HEIGHT = 10;
const STRIP_TOP = 100;

function mountIndex(available: string[] = []) {
  const wrapper = mount(AlphabetIndex, {
    attachTo: document.body,
    props: { available: new Set(available) },
  });
  const strip = wrapper.find(".alphabet-index__strip").element as HTMLElement;
  vi.spyOn(strip, "getBoundingClientRect").mockReturnValue({
    top: STRIP_TOP,
    height: ROW_HEIGHT * INDEX_LETTERS.length,
  } as DOMRect);
  return wrapper;
}

function letterButton(wrapper: VueWrapper, letter: string) {
  return wrapper.find(`[data-letter="${letter}"]`);
}

async function dispatchPointer(
  wrapper: VueWrapper,
  type: string,
  clientY: number,
) {
  wrapper.find(".alphabet-index__strip").element.dispatchEvent(
    new PointerEvent(type, {
      pointerId: 1,
      clientY,
      pointerType: "touch",
      bubbles: true,
    }),
  );
  await nextTick();
}

describe("AlphabetIndex", () => {
  it("renders '#'' plus A–Z with an accessible label", () => {
    const wrapper = mountIndex();
    const strip = wrapper.find(".alphabet-index__strip");

    expect(strip.attributes("aria-label")).toBe(
      i18n.global.t("browse.list.alphabetIndex"),
    );
    const buttons = wrapper.findAll(".alphabet-index__letter");
    expect(buttons).toHaveLength(27);
    expect(buttons[0].text()).toBe("#");
    expect(buttons[26].text()).toBe("Z");
    wrapper.unmount();
  });

  it("disables letters with no items", () => {
    const wrapper = mountIndex(["A", "M", "#"]);

    expect(letterButton(wrapper, "A").attributes("disabled")).toBeUndefined();
    expect(letterButton(wrapper, "B").attributes("disabled")).toBeDefined();
    expect(letterButton(wrapper, "#").attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("emits jump on pointer down at the letter's position", async () => {
    const wrapper = mountIndex(["M"]);

    // '#' = row 0, A = row 1, …, M = row 13.
    await dispatchPointer(
      wrapper,
      "pointerdown",
      STRIP_TOP + 13 * ROW_HEIGHT + 5,
    );

    expect(wrapper.emitted("jump")).toEqual([["M"]]);
    expect(wrapper.find(".alphabet-index__bubble").text()).toBe("M");
    wrapper.unmount();
  });

  it("emits jumps while scrubbing and hides the bubble on release", async () => {
    const wrapper = mountIndex(["A", "C", "E"]);

    await dispatchPointer(wrapper, "pointerdown", STRIP_TOP + 15);
    await dispatchPointer(wrapper, "pointermove", STRIP_TOP + 15);
    await dispatchPointer(wrapper, "pointermove", STRIP_TOP + 35);
    await dispatchPointer(wrapper, "pointermove", STRIP_TOP + 45);
    await dispatchPointer(wrapper, "pointermove", STRIP_TOP + 55);
    await dispatchPointer(wrapper, "pointerup", STRIP_TOP + 55);

    // Same letter re-enters are deduplicated; absent letters are skipped.
    expect(wrapper.emitted("jump")).toEqual([["A"], ["C"], ["E"]]);
    expect(wrapper.find(".alphabet-index__bubble").exists()).toBe(false);
    wrapper.unmount();
  });

  it("shows the letter under the pointer even when it has no items", async () => {
    const wrapper = mountIndex(["A"]);

    await dispatchPointer(wrapper, "pointerdown", STRIP_TOP + 45);

    expect(wrapper.find(".alphabet-index__bubble").text()).toBe("D");
    expect(wrapper.emitted("jump")).toBeFalsy();
    wrapper.unmount();
  });

  it("emits jump on keyboard activation", async () => {
    const wrapper = mountIndex(["B"]);

    // trigger('click') produces a detail-0 click like Enter/Space would.
    await letterButton(wrapper, "B").trigger("click");

    expect(wrapper.emitted("jump")).toEqual([["B"]]);
    // Keyboard activation doesn't leave the scrub bubble visible.
    expect(wrapper.find(".alphabet-index__bubble").exists()).toBe(false);
    wrapper.unmount();
  });

  it("moves focus between available letters with arrow keys", async () => {
    const wrapper = mountIndex(["A", "D", "Z"]);

    const a = letterButton(wrapper, "A");
    await a.trigger("keydown", { key: "ArrowDown" });
    expect((document.activeElement as HTMLElement | null)?.dataset.letter).toBe(
      "D",
    );

    await letterButton(wrapper, "D").trigger("keydown", { key: "ArrowDown" });
    expect((document.activeElement as HTMLElement | null)?.dataset.letter).toBe(
      "Z",
    );

    // Nowhere further to go — focus stays.
    await letterButton(wrapper, "Z").trigger("keydown", { key: "ArrowDown" });
    expect((document.activeElement as HTMLElement | null)?.dataset.letter).toBe(
      "Z",
    );

    await letterButton(wrapper, "Z").trigger("keydown", { key: "Home" });
    expect((document.activeElement as HTMLElement | null)?.dataset.letter).toBe(
      "A",
    );
    wrapper.unmount();
  });

  it("keeps a single tab stop on the first available letter", async () => {
    const wrapper = mountIndex(["M", "Z"]);

    expect(letterButton(wrapper, "M").attributes("tabindex")).toBe("0");
    expect(letterButton(wrapper, "Z").attributes("tabindex")).toBe("-1");

    await letterButton(wrapper, "Z").trigger("focus");
    await nextTick();

    expect(letterButton(wrapper, "Z").attributes("tabindex")).toBe("0");
    expect(letterButton(wrapper, "M").attributes("tabindex")).toBe("-1");
    wrapper.unmount();
  });
});
