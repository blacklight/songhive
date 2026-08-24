import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as tracksApi from "@/api/tracks";
import * as artistsApi from "@/api/artists";
import * as albumsApi from "@/api/albums";
import { usePlayerStore } from "@/stores/player";
import type { TrackResponse } from "@/api/tracks";
import type { ArtistResponse } from "@/api/artists";
import type { AlbumResponse } from "@/api/albums";
import TrackView from "./TrackView.vue";

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
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
      { path: "/tracks/:id", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
    ],
  });
}

function createTrack(id: string, title: string): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 3,
    disc_number: 1,
    duration: 185,
    genre: "Indie",
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
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

describe("TrackView", () => {
  let wrapper: ReturnType<typeof mount>;
  let player: ReturnType<typeof usePlayerStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    player = usePlayerStore();
    vi.spyOn(player, "playTrack");
    vi.clearAllMocks();
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland"),
    );
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(TrackView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("loads track, artist, and album on mount", async () => {
    await mountAt("/tracks/track-1");

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1");
    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1");

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("The Larks");
    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).toContain("Indie");
    expect(wrapper.text()).toContain("3:05");
  });

  it("plays the track", async () => {
    await mountAt("/tracks/track-1");

    const playButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.play"));
    expect(playButton).toBeDefined();

    await playButton?.trigger("click");
    await flushPromises();

    expect(player.playTrack).toHaveBeenCalledOnce();
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(tracksApi.getTrack).mockRejectedValue(new Error("not found"));

    await mountAt("/tracks/track-1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).not.toContain("not found");
  });

  it("reloads on route param change", async () => {
    const router = createTestRouter();
    await router.push("/tracks/track-1");
    await router.isReady();
    wrapper = mount(TrackView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-2", "Song Two"),
    );
    await router.push("/tracks/track-2");
    await flushPromises();

    expect(tracksApi.getTrack).toHaveBeenLastCalledWith("track-2");
    expect(wrapper.text()).toContain("Song Two");
  });
});
