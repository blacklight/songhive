import { describe, it, expect, beforeEach, vi } from "vitest";
import { defineComponent, h, ref } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import * as remoteApi from "@/api/remote";
import { useRemoteEntities } from "./useRemoteEntities";
import type { RemoteObject } from "@/api/remote";

vi.mock("@/api/remote", () => ({
  listRemoteObjectsWithMeta: vi.fn(),
}));

function remoteObject(id: string, name = `Remote ${id}`): RemoteObject {
  return {
    id,
    canonical_url: `https://remote.example/objects/${id}`,
    object_type: "Audio",
    resource_type: "track",
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    name,
    visibility: "public",
    unavailable: false,
    url: `/remote/track/${id}`,
  };
}

function page(items: RemoteObject[], offset: number, total: number) {
  return { items, offset, total };
}

function createList(options: {
  collection?: boolean;
  query?: string;
  sortBy?: string;
  sortDir?: "asc" | "desc";
  limit?: number;
}) {
  const collection = ref(options.collection ?? false);
  const query = ref(options.query ?? "");
  const sortBy = ref(options.sortBy ?? "created_at");
  const sortDir = ref<"asc" | "desc">(options.sortDir ?? "desc");
  const wrapper = mount(
    defineComponent({
      setup() {
        return {
          ...useRemoteEntities("track", {
            collection,
            query,
            sortBy,
            sortDir,
            limit: options.limit,
          }),
          collection,
          query,
          sortBy,
          sortDir,
        };
      },
      render: () => h("div"),
    }),
  );
  return wrapper;
}

describe("useRemoteEntities", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue(
      page([], 0, 0),
    );
  });

  it("loads the first remote page on mount with the view's sort", async () => {
    const wrapper = createList({ sortBy: "title", sortDir: "asc" });
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({
        resource_type: "track",
        limit: 20,
        offset: 0,
        sort_by: "title",
        sort_dir: "asc",
      }),
    );
    expect(wrapper.vm.hasMore).toBe(false);
  });

  it("appends remote pages on loadMore and tracks hasMore from the total", async () => {
    vi.mocked(remoteApi.listRemoteObjectsWithMeta)
      .mockResolvedValueOnce(
        page([remoteObject("r-1"), remoteObject("r-2")], 0, 5),
      )
      .mockResolvedValueOnce(
        page([remoteObject("r-3"), remoteObject("r-4")], 2, 5),
      )
      .mockResolvedValueOnce(page([remoteObject("r-5")], 4, 5));

    const wrapper = createList({ limit: 2 });
    await flushPromises();

    expect(wrapper.vm.items).toHaveLength(2);
    expect(wrapper.vm.hasMore).toBe(true);

    await wrapper.vm.loadMore();
    expect(wrapper.vm.items).toHaveLength(4);
    expect(wrapper.vm.items.map((i) => i.id)).toEqual([
      "r-1",
      "r-2",
      "r-3",
      "r-4",
    ]);
    expect(wrapper.vm.hasMore).toBe(true);
    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 2 }),
    );

    await wrapper.vm.loadMore();
    expect(wrapper.vm.items).toHaveLength(5);
    expect(wrapper.vm.hasMore).toBe(false);

    await wrapper.vm.loadMore();
    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledTimes(3);
  });

  it("keeps loaded pages when a loadMore request fails", async () => {
    vi.mocked(remoteApi.listRemoteObjectsWithMeta)
      .mockResolvedValueOnce(page([remoteObject("r-1")], 0, 3))
      .mockRejectedValueOnce(new Error("boom"));

    const wrapper = createList({ limit: 1 });
    await flushPromises();
    expect(wrapper.vm.items).toHaveLength(1);
    expect(wrapper.vm.hasMore).toBe(true);

    await wrapper.vm.loadMore();
    expect(wrapper.vm.items).toHaveLength(1);
    expect(wrapper.vm.hasMore).toBe(true);
  });

  it("resets to page 0 when the search query changes", async () => {
    vi.mocked(remoteApi.listRemoteObjectsWithMeta)
      .mockResolvedValueOnce(page([remoteObject("r-1")], 0, 3))
      .mockResolvedValueOnce(page([remoteObject("r-2")], 0, 1));

    const wrapper = createList({});
    await flushPromises();
    expect(wrapper.vm.items.map((i) => i.id)).toEqual(["r-1"]);

    wrapper.vm.query = "x";
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "x", offset: 0 }),
    );
    expect(wrapper.vm.items.map((i) => i.id)).toEqual(["r-2"]);
    expect(wrapper.vm.hasMore).toBe(false);
  });

  it("resets to page 0 when the sort changes", async () => {
    const wrapper = createList({ sortBy: "created_at" });
    await flushPromises();

    wrapper.vm.sortBy = "title";
    wrapper.vm.sortDir = "asc";
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({
        offset: 0,
        sort_by: "title",
        sort_dir: "asc",
      }),
    );
  });

  it("drops a stale loadMore response that lands after a reset", async () => {
    const pending: Array<(v: ReturnType<typeof page>) => void> = [];
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockImplementation(
      () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }),
    );

    const wrapper = createList({ limit: 1 });
    await flushPromises();
    pending[0]?.(page([remoteObject("r-1")], 0, 3));
    await flushPromises();

    void wrapper.vm.loadMore();
    await flushPromises();
    // A reset (collection toggle) lands while page 2 is in flight.
    wrapper.vm.collection = true;
    await flushPromises();
    // Page 2 resolves stale, then the reset resolves — the stale page
    // must not pollute the new filtered list.
    pending[1]?.(page([remoteObject("stale")], 1, 3));
    await flushPromises();
    pending[2]?.(page([remoteObject("collected")], 0, 1));
    await flushPromises();

    expect(wrapper.vm.items.map((i) => i.id)).toEqual(["collected"]);
  });
});
