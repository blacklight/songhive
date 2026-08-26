import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as favoritesApi from "@/api/favorites";
import * as tracksApi from "@/api/tracks";
import type { TrackResponse } from "@/api/tracks";
import type { QueueTrack } from "@/player/types";
import { useToastStore } from "@/stores/toast";
import TrackList from "@/components/library/TrackList.vue";
import FavoritesView from "./FavoritesView.vue";

vi.mock("@/api/favorites", () => ({
  listFavorites: vi.fn(),
  removeFavorite: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
  deleteTrack: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
    ],
  });
}

function createFavorite(
  id: string,
  trackId: string,
): favoritesApi.FavoriteResponse {
  return {
    id,
    track_id: trackId,
    created_at: "2024-01-15T10:30:00Z",
  };
}

function createTrack(id: string, title: string): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: null,
    disc_number: null,
    duration: 185,
    genre: null,
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
    artist: { id: "artist-1", name: "Artist One", image_url: null },
    album: {
      id: "album-1",
      title: "Album One",
      artist_id: "artist-1",
      artist: null,
      musicbrainz_id: null,
      release_year: null,
      cover_url: "https://example.com/cover.jpg",
      owner_id: "user-1",
      visibility: "public",
    },
  };
}

function asQueueTrack(track: TrackResponse): QueueTrack {
  return {
    ...track,
    artist_name: track.artist?.name ?? "",
    album_title: track.album?.title,
    artwork_url: track.album?.cover_url ?? track.artist?.image_url ?? undefined,
  };
}

describe("FavoritesView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([]);
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );
    vi.mocked(favoritesApi.removeFavorite).mockResolvedValue();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("renders resolved favorite tracks", async () => {
    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([
      createFavorite("f1", "track-1"),
      createFavorite("f2", "track-2"),
    ]);
    vi.mocked(tracksApi.getTrack)
      .mockResolvedValueOnce(createTrack("track-1", "Song One"))
      .mockResolvedValueOnce(createTrack("track-2", "Song Two"));

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(favoritesApi.listFavorites).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
    });
    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album",
    });
    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-2", {
      include: "artist,album",
    });
    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("Song Two");
  });

  it("disables non-track tabs and explains the backend gap", async () => {
    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    for (const key of ["albums", "artists", "playlists"] as const) {
      const tab = wrapper
        .findAll("button")
        .find((b) => b.text() === i18n.global.t(`pages.favorites.${key}`));
      expect(tab).toBeDefined();
      expect(tab?.attributes("disabled")).toBeDefined();
    }

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.favorites.entityTypesGated"),
    );
  });

  it("removes a favorite when toggle-favorite is emitted", async () => {
    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([
      createFavorite("f1", "track-1"),
    ]);
    const track = createTrack("track-1", "Song One");
    vi.mocked(tracksApi.getTrack).mockResolvedValue(track);

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const trackList = wrapper.findComponent(TrackList);
    trackList.vm.$emit("toggle-favorite", asQueueTrack(track));
    await flushPromises();

    expect(favoritesApi.removeFavorite).toHaveBeenCalledWith("track-1");
    expect(wrapper.text()).not.toContain("Song One");
  });

  it("loads the next page", async () => {
    const listFavorites = vi.mocked(favoritesApi.listFavorites);
    listFavorites
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createFavorite(`f${i}`, `track-${i}`),
        ),
      )
      .mockResolvedValueOnce([createFavorite("f20", "track-20")]);
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-20", "Song 20"),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();
    await loadMore?.trigger("click");
    await flushPromises();

    expect(listFavorites).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    expect(wrapper.text()).toContain("Song 20");
  });

  it("shows the empty state", async () => {
    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([]);

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("pages.favorites.empty"));
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(favoritesApi.listFavorites).mockRejectedValue(
      new Error("network failure"),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([
      createFavorite("f1", "track-1"),
    ]);
    const retry = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    expect(retry).toBeDefined();
    await retry?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("displays artist and album metadata from included track summaries", async () => {
    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([
      createFavorite("f1", "track-1"),
    ]);
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album",
    });
    expect(wrapper.text()).toContain("Artist One");
    expect(wrapper.text()).toContain("Album One");
  });

  it("keeps the loaded list and shows a toast when load more fails", async () => {
    const listFavorites = vi.mocked(favoritesApi.listFavorites);
    listFavorites
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createFavorite(`f${i}`, `track-${i}`),
        ),
      )
      .mockRejectedValueOnce(new Error("network failure"));

    const getTrack = vi.mocked(tracksApi.getTrack);
    for (let i = 0; i < 20; i++) {
      getTrack.mockResolvedValueOnce(createTrack(`track-${i}`, `Song ${i}`));
    }

    const toast = useToastStore();

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Song 0");
    expect(wrapper.text()).toContain("Song 19");

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();
    await loadMore?.trigger("click");
    await flushPromises();

    expect(listFavorites).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    expect(wrapper.text()).toContain("Song 0");
    expect(wrapper.text()).toContain("Song 19");
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("error");
    expect(toast.toasts[0].message).toContain("network failure");
  });

  it("shows an accurate empty state when all favorite tracks fail to resolve", async () => {
    vi.mocked(favoritesApi.listFavorites).mockResolvedValue([
      createFavorite("f1", "track-1"),
    ]);
    vi.mocked(tracksApi.getTrack).mockRejectedValue(new Error("missing"));

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).not.toContain(
      i18n.global.t("pages.favorites.empty"),
    );
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.favorites.emptyWithFavorites"),
    );
  });
});
