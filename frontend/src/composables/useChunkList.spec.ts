import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { defineComponent, h } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import {
  useChunkList,
  type ChunkListParams,
  type ChunkListResult,
} from "./useChunkList";

function createList(
  fetcher: (params: ChunkListParams) => Promise<ChunkListResult<string>>,
) {
  const wrapper = mount(
    defineComponent({
      setup() {
        return useChunkList(fetcher);
      },
      render: () => h("div"),
    }),
  );
  return wrapper;
}

function buildFetcher(
  results: Array<ChunkListResult<string> | Error>,
): (params: ChunkListParams) => Promise<ChunkListResult<string>> {
  let index = 0;
  return vi.fn(async (_params: ChunkListParams) => {
    void _params;
    const next = results[index++] ?? { items: [], offset: 0, total: 0 };
    if (next instanceof Error) throw next;
    return next;
  });
}

describe("useChunkList", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads the first chunk and sets hasMore/hasPrevious", async () => {
    const fetcher = buildFetcher([
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${i}`),
        offset: 0,
        total: 50,
      },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();

    expect(wrapper.vm.items.length).toBe(20);
    expect(wrapper.vm.hasMore).toBe(true);
    expect(wrapper.vm.hasPrevious).toBe(false);
    expect(fetcher).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
  });

  it("appends on loadMore and clears hasMore at the end", async () => {
    const fetcher = buildFetcher([
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${i}`),
        offset: 0,
        total: 21,
      },
      { items: ["item-20"], offset: 20, total: 21 },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    expect(wrapper.vm.hasMore).toBe(true);

    await wrapper.vm.loadMore();

    expect(wrapper.vm.items.length).toBe(21);
    expect(wrapper.vm.hasMore).toBe(false);
    expect(wrapper.vm.items[20]).toBe("item-20");
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
  });

  it("prepends on loadPrevious and exposes the combined range", async () => {
    const fetcher = buildFetcher([
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${i}`),
        offset: 0,
        total: 50,
      },
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${20 + i}`),
        offset: 20,
        total: 50,
      },
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${i}`),
        offset: 0,
        total: 50,
      },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    await wrapper.vm.loadMore();
    expect(wrapper.vm.items.length).toBe(40);
    expect(wrapper.vm.hasPrevious).toBe(false);

    await wrapper.vm.loadPrevious();

    expect(wrapper.vm.items.length).toBe(40);
    expect(wrapper.vm.hasPrevious).toBe(false);
    expect(wrapper.vm.items[0]).toBe("item-0");
  });

  it("loads a chunk around a given track", async () => {
    const fetcher = buildFetcher([
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${25 + i}`),
        offset: 25,
        total: 50,
      },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.loadAround("track-30");

    expect(wrapper.vm.items.length).toBe(20);
    expect(wrapper.vm.startOffset).toBe(25);
    expect(wrapper.vm.hasPrevious).toBe(true);
    expect(wrapper.vm.hasMore).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      around_track_id: "track-30",
      sort_by: "created_at",
      sort_dir: "desc",
    });
  });

  it("loads an arbitrary fragment with loadAt", async () => {
    const fetcher = buildFetcher([
      { items: ["a"], offset: 0, total: 70 },
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${40 + i}`),
        offset: 40,
        total: 70,
      },
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${20 + i}`),
        offset: 20,
        total: 70,
      },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    await wrapper.vm.loadAt(40);

    expect(wrapper.vm.items[0]).toBe("item-40");
    expect(wrapper.vm.startOffset).toBe(40);
    expect(wrapper.vm.hasPrevious).toBe(true);
    expect(wrapper.vm.hasMore).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 40,
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });

    // Load previous extends the window backwards without a reset.
    await wrapper.vm.loadPrevious();
    expect(wrapper.vm.items[0]).toBe("item-20");
    expect(wrapper.vm.startOffset).toBe(20);
  });

  it("lands on the latest loadAt target queued while busy", async () => {
    let release: (() => void) | undefined;
    const fetcher = vi.fn((params: ChunkListParams) => {
      if (params.offset === 10) {
        return new Promise<ChunkListResult<string>>((resolve) => {
          release = () => resolve({ items: ["ten"], offset: 10, total: 50 });
        });
      }
      return Promise.resolve({
        items: [`item-${params.offset}`],
        offset: params.offset,
        total: 50,
      });
    });
    const wrapper = createList(fetcher);
    await wrapper.vm.load();

    const pending = wrapper.vm.loadAt(10);
    wrapper.vm.loadAt(30);
    wrapper.vm.loadAt(40);
    release?.();
    await pending;
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 40 }),
    );
    expect(wrapper.vm.startOffset).toBe(40);
  });

  it("retries the last loadAt target after a failure", async () => {
    const fetcher = buildFetcher([
      { items: ["a"], offset: 0, total: 50 },
      new Error("boom"),
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${40 + i}`),
        offset: 40,
        total: 50,
      },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    await wrapper.vm.loadAt(40);
    expect(wrapper.vm.error).toBe("boom");

    await wrapper.vm.retry();
    expect(wrapper.vm.error).toBe(null);
    expect(wrapper.vm.startOffset).toBe(40);
    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 40 }),
    );
  });

  it("setLimit changes the page size and reloads from the start", async () => {
    const fetcher = buildFetcher([
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${i}`),
        offset: 0,
        total: 100,
      },
      {
        items: Array.from({ length: 20 }, (_, i) => `item-${20 + i}`),
        offset: 20,
        total: 100,
      },
      {
        items: Array.from({ length: 50 }, (_, i) => `big-${i}`),
        offset: 0,
        total: 100,
      },
    ]);
    const wrapper = createList(fetcher);
    await wrapper.vm.load();
    await wrapper.vm.loadMore();
    expect(wrapper.vm.items.length).toBe(40);

    await wrapper.vm.setLimit(50);

    expect(wrapper.vm.limit).toBe(50);
    expect(wrapper.vm.items.length).toBe(50);
    expect(wrapper.vm.startOffset).toBe(0);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 50,
      offset: 0,
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
  });

  it("setLimit is a no-op when the size is unchanged", async () => {
    const fetcher = buildFetcher([{ items: ["a"], offset: 0, total: 10 }]);
    const wrapper = createList(fetcher);
    await wrapper.vm.load();

    await wrapper.vm.setLimit(20);

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("debounces search and resets the list", async () => {
    const fetcher = buildFetcher([
      { items: ["old"], offset: 0, total: 1 },
      { items: ["search-a", "search-b"], offset: 0, total: 2 },
    ]);
    const wrapper = createList(fetcher);

    await wrapper.vm.load();
    wrapper.vm.search("query");

    expect(wrapper.vm.query).toBe("");
    expect(fetcher).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(wrapper.vm.query).toBe("query");
    expect(wrapper.vm.items).toEqual(["search-a", "search-b"]);
    expect(fetcher).toHaveBeenLastCalledWith({
      q: "query",
      limit: 20,
      offset: 0,
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
  });
});
