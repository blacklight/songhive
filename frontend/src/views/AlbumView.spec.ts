import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as albumsApi from "@/api/albums";
import * as artistsApi from "@/api/artists";
import * as tracksApi from "@/api/tracks";
import type { AlbumResponse } from "@/api/albums";
import type { ArtistResponse } from "@/api/artists";
import type { TrackResponse } from "@/api/tracks";
import AlbumView from "./AlbumView.vue";

vi.mock("@/api/albums", () => ({
  getAlbum: vi.fn(),
  deleteAlbum: vi.fn(),
}));

vi.mock("@/api/artists", () => ({
  getArtist: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  listTracks: vi.fn(),
  deleteTrack: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/hashtags/:name", component: { template: "<div/>" } },
      { path: "/genres/:name", component: { template: "<div/>" } },
    ],
  });
}

function createAlbum(
  id: string,
  title: string,
  hashtags: string[] = [],
): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    musicbrainz_id: null,
    release_year: 2024,
    cover_url: null,
    description: "A lovely album.",
    genre: null,
    owner_id: "user-1",
    visibility: "public",
    hashtags,
    genres: [],
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

function setAuthenticated(userId = "user-1") {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
  authStore.user = { id: userId, username: "alice" } as never;
}

function setAdmin(userId = "admin-1") {
  setAuthenticated(userId);
  const authStore = useAuthStore();
  authStore.role = "admin";
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
    hashtags: [],
    genres: [],
    artist: { id: "artist-1", name: "The Larks", image_url: null },
    album: {
      id: "album-1",
      title: "Meadowland",
      artist_id: "artist-1",
      artist: null,
      musicbrainz_id: null,
      release_year: 2024,
      cover_url: null,
      owner_id: "user-1",
      visibility: "public",
    },
  };
}

describe("AlbumView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland"),
    );
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(tracksApi.listTracks).mockResolvedValue([]);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(AlbumView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("loads album, artist, and tracks on mount", async () => {
    vi.mocked(tracksApi.listTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);

    await mountAt("/albums/album-1");

    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1", {
      include: "hashtags,genres",
    });
    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(tracksApi.listTracks).toHaveBeenCalledWith({
      q: "",
      album_id: "album-1",
      limit: 20,
      offset: 0,
      include: "artist,album",
    });

    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).toContain("The Larks");
    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("A lovely album.");
    expect(wrapper.find(".album-view__visibility i").classes()).toContain(
      "fa-globe",
    );
    expect(wrapper.find(".album-view__owner").text()).toContain("user-1");
  });

  it("renders album hashtags", async () => {
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland", ["rock", "indie"]),
    );
    await mountAt("/albums/album-1");

    expect(wrapper.text()).toContain("rock");
    expect(wrapper.text()).toContain("indie");
  });

  it("does not render an empty hashtag section", async () => {
    await mountAt("/albums/album-1");

    expect(wrapper.text()).not.toContain(
      i18n.global.t("browse.detail.hashtags"),
    );
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(albumsApi.getAlbum).mockRejectedValue(new Error("not found"));

    await mountAt("/albums/album-1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland"),
    );
    vi.mocked(tracksApi.listTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).not.toContain("not found");
  });

  it("loads the next page of tracks", async () => {
    const fetcher = vi.mocked(tracksApi.listTracks);
    fetcher
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createTrack(`track-${i}`, `Song ${i}`),
        ),
      )
      .mockResolvedValueOnce([createTrack("track-20", "Song 20")]);

    await mountAt("/albums/album-1");

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      album_id: "album-1",
      limit: 20,
      offset: 20,
      include: "artist,album",
    });
    expect(wrapper.text()).toContain("Song 19");
    expect(wrapper.text()).toContain("Song 20");
  });

  it("reloads on route param change", async () => {
    const router = createTestRouter();
    await router.push("/albums/album-1");
    await router.isReady();
    wrapper = mount(AlbumView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-2", "Sunset"),
    );
    await router.push("/albums/album-2");
    await flushPromises();

    expect(albumsApi.getAlbum).toHaveBeenLastCalledWith("album-2", {
      include: "hashtags,genres",
    });
    expect(wrapper.text()).toContain("Sunset");
  });

  it("shows the edit action for an admin who is not the owner", async () => {
    setAdmin("admin-1");
    await mountAt("/albums/album-1");

    expect(wrapper.text()).toContain(i18n.global.t("common.edit"));
  });
});
