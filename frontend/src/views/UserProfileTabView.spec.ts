import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { listAlbumsWithMeta } from "@/api/albums";
import { listLibrariesWithMeta } from "@/api/libraries";
import { listPlaylistsWithMeta } from "@/api/playlists";
import { listTracksWithMeta } from "@/api/tracks";
import { listUserActivities } from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import type { AlbumResponse } from "@/api/albums";
import type { LibraryResponse } from "@/api/libraries";
import type { PlaylistResponse } from "@/api/playlists";
import type { TrackResponse } from "@/api/tracks";
import UserProfileTabView from "./UserProfileTabView.vue";

vi.mock("@/api/albums", () => ({
  listAlbumsWithMeta: vi.fn(),
}));

vi.mock("@/api/libraries", () => ({
  listLibrariesWithMeta: vi.fn(),
}));

vi.mock("@/api/playlists", () => ({
  listPlaylistsWithMeta: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  listTracksWithMeta: vi.fn(),
}));

vi.mock("@/api/activities", () => ({
  getActivity: vi.fn(),
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

function createTrack(id: string, title: string): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: "album-1",
    visibility: "public",
    is_external: false,
    tags: [],
    genres: [],
  } as TrackResponse;
}

function createActivity(
  id: string,
  overrides: Partial<ActivityResponse> = {},
): ActivityResponse {
  return {
    id,
    entity_type: "user",
    entity_id: "user-1",
    activity_type: "create",
    source_type: "local",
    source_actor: "urn:songhive:user:user1",
    source_id: `https://example.com/users/user1/objects/${id}`,
    owner_user_id: "user-1",
    visibility: "public",
    content: "<p>post body</p>",
    content_source: "post body",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    liked: false,
    boosted: false,
    can_interact: true,
    ...overrides,
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
    vi.mocked(listAlbumsWithMeta).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(listLibrariesWithMeta).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
    vi.mocked(listPlaylistsWithMeta).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
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
    vi.mocked(listAlbumsWithMeta).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });

    const router = createTestRouter();
    await router.push("/user1");
    await router.isReady();
    wrapper = mount(UserProfileTabView, {
      global: { plugins: [router, i18n] },
      props: { tab: "albums" },
    });
    await flushPromises();

    expect(listAlbumsWithMeta).toHaveBeenCalledWith({
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
      const fetcher = vi.mocked(listAlbumsWithMeta);
      fetcher
        .mockResolvedValueOnce({
          items: Array.from({ length: 20 }, (_, i) =>
            createAlbum(`album-${i}`, `Album ${i}`),
          ),
          offset: 0,
          total: 40,
        })
        .mockResolvedValueOnce({
          items: [],
          offset: 20,
          total: 20,
        });

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
      const fetcher = vi.mocked(listLibrariesWithMeta);
      fetcher
        .mockResolvedValueOnce({
          items: Array.from({ length: 20 }, (_, i) =>
            createLibrary(`library-${i}`, `Library ${i}`),
          ),
          offset: 0,
          total: 40,
        })
        .mockResolvedValueOnce({
          items: [],
          offset: 20,
          total: 20,
        });

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
      const fetcher = vi.mocked(listPlaylistsWithMeta);
      fetcher
        .mockResolvedValueOnce({
          items: Array.from({ length: 20 }, (_, i) =>
            createPlaylist(`playlist-${i}`, `Playlist ${i}`),
          ),
          offset: 0,
          total: 40,
        })
        .mockResolvedValueOnce({
          items: [],
          offset: 20,
          total: 20,
        });

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

  describe("posts", () => {
    it("fetches posts including boosts but not replies by default", async () => {
      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "posts" },
      });
      await flushPromises();

      expect(listUserActivities).toHaveBeenCalledWith("user1", {
        mode: "posts",
        include_boosts: true,
        include_replies: false,
        limit: 20,
      });
    });

    it("renders the filter checkboxes and refetches on toggle", async () => {
      vi.mocked(listUserActivities).mockResolvedValue({
        activities: [createActivity("a1")],
        next_cursor: null,
      });
      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "posts" },
      });
      await flushPromises();

      const checkboxes = wrapper.findAll('input[type="checkbox"]');
      expect(checkboxes.length).toBe(2);
      expect((checkboxes[0].element as HTMLInputElement).checked).toBe(true);
      expect((checkboxes[1].element as HTMLInputElement).checked).toBe(false);

      await checkboxes[1].setValue(true);
      await flushPromises();
      expect(listUserActivities).toHaveBeenLastCalledWith("user1", {
        mode: "posts",
        include_boosts: true,
        include_replies: true,
        limit: 20,
      });

      await checkboxes[0].setValue(false);
      await flushPromises();
      expect(listUserActivities).toHaveBeenLastCalledWith("user1", {
        mode: "posts",
        include_boosts: false,
        include_replies: true,
        limit: 20,
      });
    });

    it("does not show the filters on the activity tab", async () => {
      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "activity" },
      });
      await flushPromises();

      expect(wrapper.findAll('input[type="checkbox"]').length).toBe(0);
      expect(listUserActivities).toHaveBeenCalledWith("user1", {
        mode: "all",
        include_boosts: true,
        include_replies: false,
        limit: 20,
      });
    });
  });

  describe("tracks", () => {
    it("appends the next page and hides the button when there are no more results", async () => {
      const fetcher = vi.mocked(listTracksWithMeta);
      fetcher
        .mockResolvedValueOnce({
          tracks: Array.from({ length: 20 }, (_, i) =>
            createTrack(`track-${i}`, `Track ${i}`),
          ),
          offset: 0,
          total: 40,
        })
        .mockResolvedValueOnce({
          tracks: [],
          offset: 20,
          total: 20,
        });

      const router = createTestRouter();
      await router.push("/user1");
      await router.isReady();
      wrapper = mount(UserProfileTabView, {
        global: { plugins: [router, i18n] },
        props: { tab: "tracks" },
      });
      await flushPromises();

      expect(fetcher).toHaveBeenLastCalledWith({
        owner_username: "user1",
        include: "artist,album",
        limit: 20,
        offset: 0,
      });
      expect(wrapper.text()).toContain("Track 0");
      expect(wrapper.text()).toContain("Track 19");

      const loadMore = findLoadMoreButton(wrapper);
      expect(loadMore).toBeDefined();

      await loadMore?.trigger("click");
      await flushPromises();

      expect(fetcher).toHaveBeenLastCalledWith({
        owner_username: "user1",
        include: "artist,album",
        limit: 20,
        offset: 20,
      });
      expect(wrapper.text()).toContain("Track 0");
      expect(wrapper.text()).toContain("Track 19");
      expect(findLoadMoreButton(wrapper)).toBeUndefined();
    });
  });
});
