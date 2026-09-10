import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
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
