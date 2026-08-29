import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import { listTracksWithMeta } from "@/api/tracks";
import TagDetailView, {
  type ListParams,
  type ListResult,
} from "./TagDetailView.vue";

vi.mock("@/api/tracks", () => ({
  listTracksWithMeta: vi.fn(),
}));

vi.mock("@/components/hashtags/TaggedItemCard.vue", () => ({
  default: {
    template:
      '<div class="tagged-item-card-stub" :data-type="type" :data-id="id">{{ type }}:{{ id }}</div>',
    props: ["type", "id"],
  },
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/genres", name: "genres", component: { template: "<div/>" } },
    ],
  });
}

function createItem(type: string, id: string) {
  return { type, id };
}

function createListResult(
  items: { type: string; id: string }[] = [],
  total = 0,
  offset = 0,
): ListResult {
  return { items, total, offset };
}

function createTrack(id: string, title: string) {
  return {
    id,
    title,
    artist_id: `artist-${id}`,
    artist: { id: `artist-${id}`, name: `Artist ${id}` },
    album: null,
  };
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper
    .findAll("button")
    .find((button) => button.text().includes(text));
}

describe("TagDetailView", () => {
  let wrapper: ReturnType<typeof mount>;
  let loadItems: ReturnType<typeof vi.fn>;
  let deleteItem: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    loadItems = vi.fn<
      (name: string, params: ListParams) => Promise<ListResult>
    >(() => Promise.resolve(createListResult()));
    deleteItem = vi.fn<(name: string) => Promise<unknown>>(() =>
      Promise.resolve(undefined),
    );
    vi.mocked(listTracksWithMeta).mockResolvedValue({
      tracks: [],
      total: 0,
      offset: 0,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
    vi.clearAllMocks();
  });

  async function mountView(
    props: Record<string, unknown> = {},
    query: Record<string, string> = {},
  ) {
    const router = createTestRouter();
    await router.push({ path: "/", query });
    await router.isReady();

    wrapper = mount(TagDetailView, {
      props: {
        kind: "genre",
        name: "rock",
        availableTypes: ["album", "track"],
        loadItems,
        deleteItem,
        ...props,
      },
      global: {
        plugins: [router],
        stubs: {
          TrackList: {
            template:
              '<div class="track-list-stub" :data-tracks="tracks.length">tracks:{{ tracks.length }}</div>',
            props: ["tracks", "loading"],
          },
        },
      },
    });

    await flushPromises();
  }

  it("counts each category and only shows tabs for non-empty ones", async () => {
    loadItems.mockImplementation((_name: string, params: ListParams) => {
      if (params.type === "album") {
        return Promise.resolve(
          createListResult([createItem("album", "album-1")], 3),
        );
      }
      return Promise.resolve(createListResult([], 0));
    });

    await mountView();

    expect(loadItems).toHaveBeenCalledWith(
      "rock",
      expect.objectContaining({ type: "album", limit: 1 }),
    );
    expect(loadItems).toHaveBeenCalledWith(
      "rock",
      expect.objectContaining({ type: "track", limit: 1 }),
    );
    expect(loadItems).toHaveBeenLastCalledWith(
      "rock",
      expect.objectContaining({
        type: "album",
        limit: 24,
        offset: 0,
        sort_by: "created_at",
        sort_dir: "desc",
      }),
    );
    expect(listTracksWithMeta).not.toHaveBeenCalled();

    const tabs = wrapper.findAll(".app-tabs__tab");
    expect(tabs.length).toBe(1);
    expect(tabs[0].text()).toBe("Albums");
    expect(wrapper.text()).not.toContain("Tracks");
  });

  it("shows an empty state when no category has items", async () => {
    loadItems.mockResolvedValue(createListResult([], 0));
    await mountView();

    expect(wrapper.findAll(".app-tabs__tab").length).toBe(0);
    expect(wrapper.text()).toContain("No items in this genre");
  });

  it("uses the URL query type as the active tab on mount", async () => {
    loadItems.mockImplementation((_name: string, params: ListParams) => {
      if (params.type === "album") {
        return Promise.resolve(
          createListResult([createItem("album", "album-1")], 3),
        );
      }
      if (params.type === "track" && params.limit === 1) {
        return Promise.resolve(
          createListResult([createItem("track", "track-1")], 2),
        );
      }
      return Promise.resolve(createListResult([], 0));
    });
    vi.mocked(listTracksWithMeta).mockResolvedValue({
      tracks: [createTrack("track-1", "Song One") as never],
      total: 2,
      offset: 0,
    });

    await mountView({}, { type: "track" });

    expect(listTracksWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({
        genre: "rock",
        limit: 24,
        offset: 0,
        sort_by: "created_at",
        sort_dir: "desc",
        include: "artist,album",
      }),
    );
    expect(wrapper.find(".track-list-stub").exists()).toBe(true);
  });

  it("switches visible tabs, updates the URL query, and reloads", async () => {
    loadItems.mockImplementation((_name: string, params: ListParams) => {
      if (params.type === "album") {
        return Promise.resolve(
          createListResult([createItem("album", "album-1")], 2),
        );
      }
      if (params.type === "track" && params.limit === 1) {
        return Promise.resolve(
          createListResult([createItem("track", "track-1")], 2),
        );
      }
      return Promise.resolve(createListResult([], 0));
    });
    vi.mocked(listTracksWithMeta).mockResolvedValue({
      tracks: [createTrack("track-1", "Song One") as never],
      total: 2,
      offset: 0,
    });

    await mountView();

    const trackTab = wrapper
      .findAll(".app-tabs__tab")
      .find((button) => button.text() === "Tracks");
    expect(trackTab).toBeDefined();
    await trackTab?.trigger("click");
    await flushPromises();

    expect(listTracksWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({
        genre: "rock",
        limit: 24,
        offset: 0,
        sort_by: "created_at",
        sort_dir: "desc",
        include: "artist,album",
      }),
    );
    expect(wrapper.vm?.$router?.currentRoute.value.query.type === "track").toBe(
      true,
    );
  });

  it("shows an error banner with a retry button", async () => {
    loadItems
      .mockRejectedValueOnce(new Error("network failure"))
      .mockImplementation((_name: string, params: ListParams) => {
        if (params.type === "album") {
          return Promise.resolve(
            createListResult([createItem("album", "album-1")], 2),
          );
        }
        return Promise.resolve(createListResult([], 0));
      });

    await mountView();
    expect(wrapper.text()).toContain("network failure");

    const retry = findButtonByText(wrapper, "Retry");
    expect(retry).toBeDefined();
    await retry?.trigger("click");
    await flushPromises();

    expect(wrapper.findAll(".tagged-item-card-stub").length).toBe(1);
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("paginates through results", async () => {
    loadItems.mockImplementation((_name: string, params: ListParams) => {
      if (params.type === "album" && params.limit === 1) {
        return Promise.resolve(
          createListResult([createItem("album", "album-0")], 30),
        );
      }
      if (params.type === "track" && params.limit === 1) {
        return Promise.resolve(createListResult([], 0));
      }
      if (params.offset === 0) {
        return Promise.resolve(
          createListResult(
            Array.from({ length: 24 }, (_, i) =>
              createItem("album", `album-${i}`),
            ),
            30,
          ),
        );
      }
      return Promise.resolve(
        createListResult([createItem("album", "album-24")], 30, 24),
      );
    });

    await mountView();

    const pagination = wrapper.findComponent({ name: "AppPagination" });
    expect(pagination.exists()).toBe(true);
    await pagination.vm.$emit("update:page", 2);
    await flushPromises();

    expect(loadItems).toHaveBeenLastCalledWith(
      "rock",
      expect.objectContaining({
        type: "album",
        offset: 24,
        limit: 24,
      }),
    );
  });

  it("toggles the sort direction", async () => {
    loadItems.mockImplementation((_name: string, params: ListParams) => {
      if (params.type === "album" && params.limit === 1) {
        return Promise.resolve(
          createListResult([createItem("album", "album-1")], 1),
        );
      }
      return Promise.resolve(createListResult([], 0));
    });

    await mountView();

    const sortControl = wrapper.findComponent({ name: "SortControl" });
    await sortControl?.vm.$emit("update:direction", "asc");
    await flushPromises();

    expect(loadItems).toHaveBeenLastCalledWith(
      "rock",
      expect.objectContaining({
        sort_dir: "asc",
      }),
    );
  });

  it("allows an admin to delete the tag and redirect", async () => {
    const authStore = useAuthStore();
    authStore.role = "admin";
    authStore.accessToken = "token";
    authStore.refreshToken = "refresh";
    authStore.expiresAt = Date.now() + 10000;
    authStore.status = "authenticated";

    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);

    loadItems.mockImplementation((_name: string, params: ListParams) => {
      if (params.type === "album" && params.limit === 1) {
        return Promise.resolve(
          createListResult([createItem("album", "album-1")], 1),
        );
      }
      return Promise.resolve(createListResult([], 0));
    });

    await mountView();

    const deleteButton = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Delete"));
    expect(deleteButton).toBeDefined();
    await deleteButton?.trigger("click");
    await flushPromises();

    expect(confirm.open).toHaveBeenCalled();
    expect(deleteItem).toHaveBeenCalledWith("rock");

    const toast = useToastStore();
    expect(toast.toasts.some((t) => t.message === "Genre deleted.")).toBe(true);
  });
});
