import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { useMediaQuery } from "./useMediaQuery";

const TestComponent = defineComponent({
  props: {
    query: { type: String, required: true },
    defaultValue: { type: Boolean, default: false },
  },
  setup(props) {
    const matches = useMediaQuery(props.query, props.defaultValue);
    return () => h("span", String(matches.value));
  },
});

describe("useMediaQuery", () => {
  let matchMediaMock: ReturnType<typeof vi.fn>;
  let listeners: EventListener[] = [];

  beforeEach(() => {
    listeners = [];
    matchMediaMock = vi.fn((query: string) => ({
      matches: query === "(min-width: 1280px)",
      media: query,
      addEventListener: (_event: string, listener: EventListener) => {
        listeners.push(listener);
      },
      removeEventListener: (_event: string, listener: EventListener) => {
        listeners = listeners.filter((l) => l !== listener);
      },
      dispatchEvent: (event: Event) => {
        listeners.forEach((listener) => listener(event));
      },
    }));

    window.matchMedia = matchMediaMock;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Reflect.deleteProperty(window, "matchMedia");
  });

  it("returns the default value when matchMedia is unavailable", () => {
    Reflect.deleteProperty(window, "matchMedia");

    const wrapper = mount(TestComponent, {
      props: { query: "(min-width: 800px)", defaultValue: true },
    });
    expect(wrapper.text()).toBe("true");
  });

  it("reflects the initial matchMedia result", async () => {
    const wrapper = mount(TestComponent, {
      props: { query: "(min-width: 1280px)", defaultValue: false },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toBe("true");
  });
});
