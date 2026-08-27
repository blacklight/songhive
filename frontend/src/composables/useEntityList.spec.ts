import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { defineComponent, h } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { useEntityList, type EntityListParams } from "./useEntityList";

function createList(fetcher: (params: EntityListParams) => Promise<string[]>) {
  const wrapper = mount(
    defineComponent({
      setup() {
        return useEntityList(fetcher);
      },
      render: () => h("div"),
    }),
  );
  return wrapper;
}

function buildFetcher(
  results: Array<string[] | Error>,
): (params: EntityListParams) => Promise<string[]> {
  let index = 0;
  return vi.fn(async (_params: EntityListParams) => {
    void _params;
    const next = results[index++] ?? [];
    if (next instanceof Error) throw next;
    return next;
  });
}

describe("useEntityList", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads the first page and sets hasMore", async () => {
    const fetcher = buildFetcher([
      Array.from({ length: 20 }, (_, i) => `item-${i}`),
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();

    expect(wrapper.vm.items.length).toBe(20);
    expect(wrapper.vm.hasMore).toBe(true);
    expect(wrapper.vm.loading).toBe(false);
    expect(fetcher).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
  });

  it("appends on loadMore and clears hasMore when the page is short", async () => {
    const fetcher = buildFetcher([
      Array.from({ length: 20 }, (_, i) => `item-${i}`),
      ["item-20", "item-21"],
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    expect(wrapper.vm.hasMore).toBe(true);

    await wrapper.vm.loadMore();

    expect(wrapper.vm.items.length).toBe(22);
    expect(wrapper.vm.hasMore).toBe(false);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      sort_by: "name",
      sort_dir: "asc",
    });
  });

  it("debounces search and resets the list", async () => {
    const fetcher = buildFetcher([["first-page"], ["search-a", "search-b"]]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    wrapper.vm.search("query");

    expect(wrapper.vm.query).toBe("");
    expect(fetcher).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(wrapper.vm.query).toBe("query");
    expect(wrapper.vm.offset).toBe(0);
    expect(wrapper.vm.items).toEqual(["search-a", "search-b"]);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "query",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
  });

  it("sets error and stops loading on a failed fetch", async () => {
    const fetcher = buildFetcher([new Error("network failure")]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();

    expect(wrapper.vm.error).toContain("network failure");
    expect(wrapper.vm.loading).toBe(false);
    expect(wrapper.vm.hasMore).toBe(false);
  });

  it("retries the last failed page without resetting", async () => {
    const fetcher = buildFetcher([
      Array.from({ length: 20 }, (_, i) => `item-${i}`),
      new Error("retry me"),
      ["recovered"],
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    await wrapper.vm.loadMore();

    expect(wrapper.vm.error).toContain("retry me");
    expect(wrapper.vm.items.length).toBe(20);
    expect(wrapper.vm.offset).toBe(20);

    await wrapper.vm.retry();

    expect(wrapper.vm.items.length).toBe(21);
    expect(wrapper.vm.error).toBeNull();
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      sort_by: "name",
      sort_dir: "asc",
    });
  });

  it("refresh resets and reloads the list", async () => {
    const fetcher = buildFetcher([["old"], ["new"]]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    await wrapper.vm.refresh();

    expect(wrapper.vm.items).toEqual(["new"]);
    expect(wrapper.vm.offset).toBe(0);
  });

  it("retry replays the last load as a reset after a failed refresh", async () => {
    const fetcher = buildFetcher([
      ["old"],
      new Error("refresh failed"),
      ["new"],
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    await wrapper.vm.refresh();

    expect(wrapper.vm.error).toContain("refresh failed");
    expect(wrapper.vm.items).toEqual(["old"]);
    expect(wrapper.vm.offset).toBe(0);

    await wrapper.vm.retry();

    expect(wrapper.vm.error).toBeNull();
    expect(wrapper.vm.items).toEqual(["new"]);
    expect(wrapper.vm.offset).toBe(0);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
  });

  it("does not increment offset when loadMore is called during an in-flight load", async () => {
    const fetcher = buildFetcher([
      Array.from({ length: 20 }, (_, i) => `item-${i}`),
      ["item-20"],
    ]);
    const wrapper = createList(fetcher);

    const loadPromise = wrapper.vm.load();
    wrapper.vm.loadMore();
    await loadPromise;
    await flushPromises();

    expect(wrapper.vm.offset).toBe(0);
    expect(wrapper.vm.items.length).toBe(20);
    expect(fetcher).toHaveBeenCalledTimes(1);

    await wrapper.vm.loadMore();

    expect(wrapper.vm.offset).toBe(20);
    expect(wrapper.vm.items.length).toBe(21);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      sort_by: "name",
      sort_dir: "asc",
    });
  });
});
