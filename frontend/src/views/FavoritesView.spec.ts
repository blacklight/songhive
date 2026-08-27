import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as tracksApi from "@/api/tracks";
import * as favoritesApi from "@/api/favorites";
import type { TrackResponse, ListTracksResult } from "@/api/tracks";
import type { QueueTrack } from "@/player/types";
import TrackList from "@/components/library/TrackList.vue";
import FavoritesView from "./FavoritesView.vue";

vi.mock("@/api/tracks", () => ({
  listTracksWithMeta: vi.fn(),
  listTracks: vi.fn(),
  getTrack: vi.fn(),
  deleteTrack: vi.fn(),
  deleteTracks: vi.fn(),
  enrichTrack: vi.fn(),
  downloadTrack: vi.fn(),
}));

vi.mock("@/api/favorites", () => ({
  removeFavorite: vi.fn(),
  addFavorite: vi.fn(),
  listFavorites: vi.fn(),
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

function createTrack(
  id: string,
  title: string,
  artistName?: string,
  albumTitle?: string,
): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 1,
    disc_number: null,
    duration: 185,
    genre: null,
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
    artist: artistName
      ? { id: "artist-1", name: artistName, image_url: null }
      : null,
    album: albumTitle
      ? {
          id: "album-1",
          title: albumTitle,
          artist_id: "artist-1",
          artist: null,
          musicbrainz_id: null,
          release_year: 2024,
          cover_url: null,
          owner_id: "user-1",
          visibility: "public",
        }
      : null,
  };
}

function createListResult(
  tracks: TrackResponse[],
  total?: number,
): ListTracksResult {
  return {
    tracks,
    offset: 0,
    total: total ?? tracks.length,
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

function listCall(offset = 0, q = "") {
  return {
    q,
    limit: 20,
    offset,
    around_track_id: undefined,
    sort_by: "created_at",
    sort_dir: "desc",
    favorited: true,
    include: "artist,album",
  };
}

describe("FavoritesView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([]),
    );
    vi.mocked(favoritesApi.removeFavorite).mockResolvedValue();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("renders resolved favorite tracks", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([
        createTrack("track-1", "Song One"),
        createTrack("track-2", "Song Two"),
      ]),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(tracksApi.listTracksWithMeta).toHaveBeenCalledWith(listCall());
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
    const track = createTrack("track-1", "Song One");
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([track]),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const trackList = wrapper.findComponent(TrackList);
    expect(trackList.exists()).toBe(true);

    trackList.vm.$emit("toggle-favorite", asQueueTrack(track));
    await flushPromises();

    expect(favoritesApi.removeFavorite).toHaveBeenCalledWith("track-1");
    expect(wrapper.text()).not.toContain("Song One");
  });

  it("loads the next page", async () => {
    const fetcher = vi.mocked(tracksApi.listTracksWithMeta);
    fetcher
      .mockResolvedValueOnce(
        createListResult(
          Array.from({ length: 20 }, (_, i) =>
            createTrack(`track-${i}`, `Song ${i}`),
          ),
          21,
        ),
      )
      .mockResolvedValueOnce(
        createListResult([createTrack("track-20", "Song 20")], 21),
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

    expect(fetcher).toHaveBeenLastCalledWith(listCall(20));
    expect(wrapper.text()).toContain("Song 19");
    expect(wrapper.text()).toContain("Song 20");
  });

  it("shows the empty state", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([]),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("pages.favorites.empty"));
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockRejectedValue(
      new Error("network failure"),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([createTrack("track-1", "Song One")]),
    );
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
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([
        createTrack("track-1", "Song One", "The Artist", "The Album"),
      ]),
    );

    wrapper = mount(FavoritesView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(tracksApi.listTracksWithMeta).toHaveBeenCalledWith(listCall());
    expect(wrapper.text()).toContain("The Artist");
    expect(wrapper.text()).toContain("The Album");
  });

  it("shows an error when loading the next page fails", async () => {
    const fetcher = vi.mocked(tracksApi.listTracksWithMeta);
    fetcher
      .mockResolvedValueOnce(
        createListResult(
          Array.from({ length: 20 }, (_, i) =>
            createTrack(`track-${i}`, `Song ${i}`),
          ),
          21,
        ),
      )
      .mockRejectedValueOnce(new Error("network failure"));

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

    expect(fetcher).toHaveBeenLastCalledWith(listCall(20));
    expect(wrapper.text()).toContain("network failure");
  });
});
