import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as tracksApi from "@/api/tracks";
import type { TrackResponse, ListTracksResult } from "@/api/tracks";
import TracksView from "./TracksView.vue";

vi.mock("@/api/tracks", () => ({
  listTracksWithMeta: vi.fn(),
  deleteTrack: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/tracks/:id", component: { template: "<div/>" } },
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
          release_year: null,
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

describe("TracksView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([]),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("fetches tracks on mount", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([createTrack("track-1", "Song One")]),
    );

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(tracksApi.listTracksWithMeta).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      include: "artist,album",
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
    expect(wrapper.text()).toContain("Song One");
  });

  it("fetches and displays artist and album names", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([
        createTrack("track-1", "Song One", "The Artist", "The Album"),
      ]),
    );

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(tracksApi.listTracksWithMeta).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      include: "artist,album",
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
    expect(wrapper.text()).toContain("The Artist");
    expect(wrapper.text()).toContain("The Album");
  });

  it("shows the empty state", async () => {
    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.tracks"),
      }),
    );
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockRejectedValue(
      new Error("network failure"),
    );

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("network failure");

    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([createTrack("track-1", "Song One")]),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).not.toContain("network failure");
  });

  it("debounces search and resets the list", async () => {
    const fetcher = vi.mocked(tracksApi.listTracksWithMeta);
    fetcher
      .mockResolvedValueOnce(
        createListResult([createTrack("track-1", "First Song")]),
      )
      .mockResolvedValueOnce(
        createListResult([createTrack("track-2", "Searched Song")]),
      );

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("query");

    vi.advanceTimersByTime(0);
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "query",
      limit: 20,
      offset: 0,
      include: "artist,album",
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
    expect(wrapper.text()).toContain("Searched Song");
    expect(wrapper.text()).not.toContain("First Song");
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

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      include: "artist,album",
      around_track_id: undefined,
      sort_by: "created_at",
      sort_dir: "desc",
    });
    expect(wrapper.text()).toContain("Song 19");
    expect(wrapper.text()).toContain("Song 20");
  });
});
