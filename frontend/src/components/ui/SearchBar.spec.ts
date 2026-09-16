import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { nextTick } from "vue";
import SearchBar from "./SearchBar.vue";
import type { SearchResultSection } from "@/api/search";

const allEntities: SearchResultSection[] = [
  {
    entity: "tracks",
    total: 1,
    items: [
      {
        type: "track",
        id: "track-1",
        name: "Waiting Room",
        title: "Waiting Room",
        subtitle: "Fugazi",
        url: "/tracks/track-1",
        image_url: null,
      },
    ],
  },
];

const multiEntities: SearchResultSection[] = [
  {
    entity: "tracks",
    total: 2,
    items: [
      {
        type: "track",
        id: "track-1",
        name: "Waiting Room",
        title: "Waiting Room",
        subtitle: "Fugazi",
        url: "/tracks/track-1",
        image_url: null,
      },
      {
        type: "track",
        id: "track-2",
        name: "Merchandise",
        title: "Merchandise",
        subtitle: "Fugazi",
        url: "/tracks/track-2",
        image_url: null,
      },
    ],
  },
  {
    entity: "artists",
    total: 1,
    items: [
      {
        type: "artist",
        id: "artist-1",
        name: "Fugazi",
        title: "Fugazi",
        subtitle: null,
        url: "/artists/artist-1",
        image_url: null,
      },
    ],
  },
];

async function openSuggestions(wrapper: VueWrapper) {
  const input = wrapper.find("input");
  await input.setValue("Fug");
  await nextTick();
  vi.advanceTimersByTime(100);
  await flushPromises();
  return input;
}

describe("SearchBar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits update:modelValue after the debounce window", async () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounce: 300 },
    });

    const input = wrapper.find("input");
    await input.setValue("hello");
    await nextTick();

    expect(wrapper.emitted("update:modelValue")).toBeFalsy();
    vi.advanceTimersByTime(300);

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["hello"]);
    wrapper.unmount();
  });

  it("emits search immediately on Enter", async () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounce: 300 },
    });

    const input = wrapper.find("input");
    await input.setValue("now");
    await input.trigger("keydown", { key: "Enter" });
    await nextTick();

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["now"]);
    expect(wrapper.emitted("search")?.[0]).toEqual(["now"]);
    wrapper.unmount();
  });

  it("clears the value and emits an empty update", async () => {
    const wrapper = mount(SearchBar, {
      props: { modelValue: "", debounce: 300 },
    });

    const input = wrapper.find("input");
    await input.setValue("clear me");
    await nextTick();

    const clearButton = wrapper.find(".search-bar__clear");
    await clearButton.trigger("click");
    await nextTick();

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([""]);
    expect(input.element.value).toBe("");
    wrapper.unmount();
  });

  it("does not call the autocomplete fetcher when autocomplete is disabled", async () => {
    const fetcher = vi.fn().mockResolvedValue([]);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: false,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("hello");
    await nextTick();
    vi.advanceTimersByTime(300);

    expect(fetcher).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("does not fetch until the input reaches the minimum length", async () => {
    const fetcher = vi.fn().mockResolvedValue([]);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteMinLength: 3,
        autocompleteDelay: 300,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("ab");
    await nextTick();
    vi.advanceTimersByTime(300);

    expect(fetcher).not.toHaveBeenCalled();

    await input.setValue("abc");
    await nextTick();
    vi.advanceTimersByTime(300);

    expect(fetcher).toHaveBeenCalledOnce();
    wrapper.unmount();
  });

  it("debounces autocomplete requests independently of model updates", async () => {
    const fetcher = vi.fn().mockResolvedValue([]);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        debounce: 300,
        autocomplete: true,
        autocompleteDelay: 150,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("ab");
    await nextTick();

    expect(wrapper.emitted("update:modelValue")).toBeFalsy();
    expect(fetcher).not.toHaveBeenCalled();

    vi.advanceTimersByTime(150);
    expect(fetcher).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(150);
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["ab"]);
    wrapper.unmount();
  });

  it("ignores stale autocomplete responses", async () => {
    const fetcher = vi
      .fn()
      .mockImplementationOnce(async () => {
        await new Promise((resolve) => setTimeout(resolve, 500));
        return allEntities;
      })
      .mockResolvedValueOnce([]);

    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("one");
    await nextTick();
    vi.advanceTimersByTime(100);

    await input.setValue("two");
    await nextTick();
    vi.advanceTimersByTime(100);

    vi.advanceTimersByTime(500);
    await nextTick();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(wrapper.findAll(".search-suggestions__section").length).toBe(0);
    wrapper.unmount();
  });

  it("renders suggestions and emits select-suggestion on click", async () => {
    const fetcher = vi.fn().mockResolvedValue(allEntities);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("Fug");
    await nextTick();
    vi.advanceTimersByTime(100);
    await flushPromises();

    expect(wrapper.findAll(".search-suggestions__item").length).toBe(1);
    await wrapper.find(".search-suggestions__item").trigger("click");
    await nextTick();

    expect(wrapper.emitted("select-suggestion")?.[0]).toEqual([
      allEntities[0].items[0],
    ]);
    expect(wrapper.find(".search-suggestions").exists()).toBe(false);
    wrapper.unmount();
  });

  it("closes the popover on Escape without emitting search", async () => {
    const fetcher = vi.fn().mockResolvedValue(allEntities);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("Fug");
    await nextTick();
    vi.advanceTimersByTime(100);
    await flushPromises();

    expect(wrapper.find(".search-suggestions").exists()).toBe(true);
    await input.trigger("keydown", { key: "Escape" });
    await nextTick();

    expect(wrapper.emitted("search")).toBeFalsy();
    expect(wrapper.find(".search-suggestions").exists()).toBe(false);
    wrapper.unmount();
  });

  it("fetches suggestions for a bare '#' when tags are searchable", async () => {
    const fetcher = vi.fn().mockResolvedValue([]);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteEntities: ["tracks", "tags"],
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("#");
    await nextTick();
    vi.advanceTimersByTime(100);

    expect(fetcher).toHaveBeenCalledWith("#", ["tracks", "tags"], 5);
    wrapper.unmount();
  });

  it("does not treat '#' specially when tags are not searchable", async () => {
    const fetcher = vi.fn().mockResolvedValue([]);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteEntities: ["tracks"],
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("#");
    await nextTick();
    vi.advanceTimersByTime(100);

    expect(fetcher).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("moves the highlight with arrow keys and selects with Enter", async () => {
    const fetcher = vi.fn().mockResolvedValue(multiEntities);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = await openSuggestions(wrapper);
    const items = wrapper.findAll(".search-suggestions__item");
    expect(items.length).toBe(3);

    await input.trigger("keydown", { key: "ArrowDown" });
    await nextTick();
    expect(items[0].classes()).toContain("search-suggestions__item--active");

    // Crosses into the next section and wraps around.
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "ArrowDown" });
    await nextTick();
    expect(items[2].classes()).toContain("search-suggestions__item--active");
    await input.trigger("keydown", { key: "ArrowDown" });
    await nextTick();
    expect(items[0].classes()).toContain("search-suggestions__item--active");

    await input.trigger("keydown", { key: "Enter" });
    await nextTick();

    expect(wrapper.emitted("select-suggestion")?.[0]).toEqual([
      multiEntities[0].items[0],
    ]);
    expect(wrapper.emitted("search")).toBeFalsy();
    expect(wrapper.find(".search-suggestions").exists()).toBe(false);
    wrapper.unmount();
  });

  it("wraps the highlight to the last item on ArrowUp", async () => {
    const fetcher = vi.fn().mockResolvedValue(multiEntities);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = await openSuggestions(wrapper);
    await input.trigger("keydown", { key: "ArrowUp" });
    await nextTick();

    const items = wrapper.findAll(".search-suggestions__item");
    expect(items[2].classes()).toContain("search-suggestions__item--active");

    await input.trigger("keydown", { key: "Enter" });
    await nextTick();
    expect(wrapper.emitted("select-suggestion")?.[0]).toEqual([
      multiEntities[1].items[0],
    ]);
    wrapper.unmount();
  });

  it("still emits search on Enter when no suggestion is highlighted", async () => {
    const fetcher = vi.fn().mockResolvedValue(multiEntities);
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = await openSuggestions(wrapper);
    expect(wrapper.find(".search-suggestions").exists()).toBe(true);

    await input.trigger("keydown", { key: "Enter" });
    await nextTick();

    expect(wrapper.emitted("select-suggestion")).toBeFalsy();
    expect(wrapper.emitted("search")?.[0]).toEqual(["Fug"]);
    wrapper.unmount();
  });

  it("emits autocomplete-error and shows an error state", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("network"));
    const wrapper = mount(SearchBar, {
      props: {
        modelValue: "",
        autocomplete: true,
        autocompleteDelay: 100,
        autocompleteFetcher: fetcher,
      },
    });

    const input = wrapper.find("input");
    await input.setValue("fail");
    await nextTick();
    vi.advanceTimersByTime(100);
    await flushPromises();

    expect(wrapper.emitted("autocomplete-error")?.[0]).toEqual([
      new Error("network"),
    ]);
    expect(wrapper.find(".search-suggestions__error").exists()).toBe(true);
    wrapper.unmount();
  });
});
