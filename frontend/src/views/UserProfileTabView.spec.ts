import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { listAlbums } from "@/api/albums";
import { listLibraries } from "@/api/libraries";
import { listPlaylists } from "@/api/playlists";
import { listTracksWithMeta } from "@/api/tracks";
import { listUserActivities } from "@/api/activities";
import type { AlbumResponse } from "@/api/albums";
import type { LibraryResponse } from "@/api/libraries";
import type { PlaylistResponse } from "@/api/playlists";
import UserProfileTabView from "./UserProfileTabView.vue";

vi.mock("@/api/albums", () => ({
  listAlbums: vi.fn(),
}));

vi.mock("@/api/libraries", () => ({
  listLibraries: vi.fn(),
}));

vi.mock("@/api/playlists", () => ({
  listPlaylists: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  listTracksWithMeta: vi.fn(),
}));

vi.mock("@/api/activities", () => ({
  listUserActivities: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/libraries/:id", component: { template: "<div/>" } },
      { path: "/playlists/:id", component: { template: "<div/>" } },
    ],
  });
}

function createAlbum(id: string, title: string): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    release_year: null,
    cover_url: null,
    visibility: "public",
  };
}

function createLibrary(id: string, name: string): LibraryResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: "",
    visibility: "public",
    can_write: false,
    image_url: null,
    cover_url: null,
    tags: [],
  };
}

function createPlaylist(id: string, name: string): PlaylistResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: "",
    visibility: "public",
    image_url: null,
    cover_url: null,
    tags: [],
  };
}

function findLoadMoreButton(wrapper: ReturnType<typeof mount>) {
  return wrapper
    .findAll("button")
    .find((b) => b.text() === i18n.global.t("common.loadMore"));
}

describe("UserProfileTabView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(listAlbums).mockResolvedValue([]);
    vi.mocked(listLibraries).mockResolvedValue([]);
    vi.mocked(listPlaylists).mockResolvedValue([]);
    vi.mocked(listTracksWithMeta).mockResolvedValue({
      tracks: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(listUserActivities).mockResolvedValue({
      activities: [],
      next_cursor: null,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("shows an entity-specific empty state", async () => {
    vi.mocked(listAlbums).mockResolvedValue([]);

    const router = createTestRouter();
    await router.push("/user1");
    await router.isReady();
    wrapper = mount(UserProfileTabView, {
      global: { plugins: [router, i18n] },
      props: { tab: "albums" },
    });
    await flushPromises();

    expect(listAlbums).toHaveBeenCalledWith({
      owner_username: "user1",
      limit: 20,
      offset: 0,
    });
    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.albums"),
      }),
    );
  });

  describe("albums", () => {
    it("appends the next page and hides the button when there are no more results", async () => {
      const fetcher = vi.mocked(listAlbums);
      fetcher
        .mockResolvedValueOnce(
          Array.from({ length: 20 }, (_, i) =>
            createAlbum(`album-${i}`, `Album ${i}`),
          ),
        )
        .mockResolvedValueOnce([]);

      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "albums" },
      });
      await flushPromises();

      expect(fetcher).toHaveBeenLastCalledWith({
        owner_username: "user1",
        limit: 20,
        offset: 0,
      });
      expect(wrapper.text()).toContain("Album 0");
      expect(wrapper.text()).toContain("Album 19");

      const loadMore = findLoadMoreButton(wrapper);
      expect(loadMore).toBeDefined();

      await loadMore?.trigger("click");
      await flushPromises();

      expect(fetcher).toHaveBeenLastCalledWith({
        owner_username: "user1",
        limit: 20,
        offset: 20,
      });
      expect(wrapper.text()).toContain("Album 0");
      expect(wrapper.text()).toContain("Album 19");
      expect(findLoadMoreButton(wrapper)).toBeUndefined();
    });
  });

  describe("libraries", () => {
    it("appends the next page and hides the button when there are no more results", async () => {
      const fetcher = vi.mocked(listLibraries);
      fetcher
        .mockResolvedValueOnce(
          Array.from({ length: 20 }, (_, i) =>
            createLibrary(`library-${i}`, `Library ${i}`),
          ),
        )
        .mockResolvedValueOnce([]);

      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "libraries" },
      });
      await flushPromises();

      expect(wrapper.text()).toContain("Library 0");

      const loadMore = findLoadMoreButton(wrapper);
      await loadMore?.trigger("click");
      await flushPromises();

      expect(fetcher).toHaveBeenLastCalledWith({
        owner_username: "user1",
        limit: 20,
        offset: 20,
      });
      expect(wrapper.text()).toContain("Library 0");
      expect(wrapper.text()).toContain("Library 19");
      expect(findLoadMoreButton(wrapper)).toBeUndefined();
    });
  });

  describe("playlists", () => {
    it("appends the next page and hides the button when there are no more results", async () => {
      const fetcher = vi.mocked(listPlaylists);
      fetcher
        .mockResolvedValueOnce(
          Array.from({ length: 20 }, (_, i) =>
            createPlaylist(`playlist-${i}`, `Playlist ${i}`),
          ),
        )
        .mockResolvedValueOnce([]);

      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "playlists" },
      });
      await flushPromises();

      expect(wrapper.text()).toContain("Playlist 0");

      const loadMore = findLoadMoreButton(wrapper);
      await loadMore?.trigger("click");
      await flushPromises();

      expect(fetcher).toHaveBeenLastCalledWith({
        owner_username: "user1",
        limit: 20,
        offset: 20,
      });
      expect(wrapper.text()).toContain("Playlist 0");
      expect(wrapper.text()).toContain("Playlist 19");
      expect(findLoadMoreButton(wrapper)).toBeUndefined();
    });
  });
});
