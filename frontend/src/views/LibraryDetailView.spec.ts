import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as librariesApi from "@/api/libraries";
import * as artistsApi from "@/api/artists";
import * as albumsApi from "@/api/albums";
import type { LibraryResponse } from "@/api/libraries";
import type { ArtistResponse } from "@/api/artists";
import type { AlbumResponse } from "@/api/albums";
import type { TrackResponse } from "@/api/tracks";
import TrackList from "@/components/library/TrackList.vue";
import LibraryDetailView from "./LibraryDetailView.vue";

vi.mock("@/api/libraries", () => ({
  getLibrary: vi.fn(),
  listLibraryTracks: vi.fn(),
}));

vi.mock("@/api/artists", () => ({
  getArtist: vi.fn(),
}));

vi.mock("@/api/albums", () => ({
  getAlbum: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/libraries/:id", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
    ],
  });
}

function createLibrary(id: string, name: string): LibraryResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: "Main music library.",
    visibility: "public",
  };
}

function createArtist(id: string, name: string): ArtistResponse {
  return {
    id,
    name,
    musicbrainz_id: null,
    bio: null,
    image_file_id: null,
    image_url: null,
  };
}

function createAlbum(id: string, title: string): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    musicbrainz_id: null,
    release_year: 2024,
    cover_url: null,
    description: null,
    owner_id: "user-1",
    visibility: "public",
  };
}

function createTrack(id: string, title: string): TrackResponse {
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
  };
}

describe("LibraryDetailView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(librariesApi.getLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library"),
    );
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([]);
    vi.mocked(artistsApi.getArtist).mockRejectedValue(new Error("not found"));
    vi.mocked(albumsApi.getAlbum).mockRejectedValue(new Error("not found"));
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(LibraryDetailView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("loads library and tracks on mount", async () => {
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);

    await mountAt("/libraries/library-1");

    expect(librariesApi.getLibrary).toHaveBeenCalledWith("library-1");
    expect(librariesApi.listLibraryTracks).toHaveBeenCalledWith("library-1", {
      limit: 20,
      offset: 0,
    });

    expect(wrapper.text()).toContain("Main Library");
    expect(wrapper.text()).toContain("Main music library.");
    expect(wrapper.text()).toContain("Song One");
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(librariesApi.getLibrary).mockRejectedValue(
      new Error("not found"),
    );

    await mountAt("/libraries/library-1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(librariesApi.getLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library"),
    );
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Main Library");
    expect(wrapper.text()).not.toContain("not found");
  });

  it("loads the next page", async () => {
    const fetcher = vi.mocked(librariesApi.listLibraryTracks);
    fetcher
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createTrack(`track-${i}`, `Song ${i}`),
        ),
      )
      .mockResolvedValueOnce([createTrack("track-20", "Song 20")]);

    await mountAt("/libraries/library-1");

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith("library-1", {
      limit: 20,
      offset: 20,
    });
    expect(wrapper.text()).toContain("Song 19");
    expect(wrapper.text()).toContain("Song 20");
  });

  it("enriches tracks with artist and album metadata for the TrackList", async () => {
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland"),
    );

    await mountAt("/libraries/library-1");
    await flushPromises();

    const trackList = wrapper.findComponent(TrackList);
    expect(trackList.exists()).toBe(true);

    const enrich = trackList.props("enrich") as Map<
      string,
      { artist_name?: string; album_title?: string }
    >;
    expect(enrich?.get("track-1")).toEqual({
      artist_name: "The Larks",
      album_title: "Meadowland",
      artwork_url: undefined,
    });
    expect(wrapper.text()).toContain("The Larks");
    expect(wrapper.text()).toContain("Meadowland");
  });

  it("reloads on route param change", async () => {
    const router = createTestRouter();
    await router.push("/libraries/library-1");
    await router.isReady();
    wrapper = mount(LibraryDetailView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(librariesApi.getLibrary).mockResolvedValue(
      createLibrary("library-2", "Secondary"),
    );
    await router.push("/libraries/library-2");
    await flushPromises();

    expect(librariesApi.getLibrary).toHaveBeenLastCalledWith("library-2");
    expect(wrapper.text()).toContain("Secondary");
  });
});
