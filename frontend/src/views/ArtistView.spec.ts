import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import * as artistsApi from "@/api/artists";
import * as albumsApi from "@/api/albums";
import * as tracksApi from "@/api/tracks";
import type { ArtistResponse } from "@/api/artists";
import type { AlbumResponse } from "@/api/albums";
import type { TrackResponse } from "@/api/tracks";
import ArtistView from "./ArtistView.vue";

vi.mock("@/api/artists", () => ({
  getArtist: vi.fn(),
  getArtistStats: vi.fn(),
  deleteArtist: vi.fn(),
}));

vi.mock("@/api/albums", () => ({
  listAlbums: vi.fn(),
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
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/tags/:name", component: { template: "<div/>" } },
    ],
  });
}

function createArtist(
  id: string,
  name: string,
  tags: string[] = [],
): ArtistResponse {
  return {
    id,
    name,
    musicbrainz_id: null,
    bio: "A great artist.",
    image_file_id: null,
    image_url: null,
    tags,
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
    tags: [],
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

function setAuthenticated(userId = "user-1") {
  const authStore = useAuthStore();
  authStore.status = "authenticated";
  authStore.user = { id: userId, username: "alice" } as never;
}

function setAdmin(userId = "admin-1") {
  setAuthenticated(userId);
  const authStore = useAuthStore();
  authStore.role = "admin";
}

describe("ArtistView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(artistsApi.getArtistStats).mockResolvedValue({
      track_count: 0,
      album_count: 0,
    });
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([]);
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
    wrapper = mount(ArtistView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("loads artist, albums, and tracks on mount", async () => {
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      createAlbum("album-1", "Meadowland"),
    ]);
    vi.mocked(tracksApi.listTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);

    await mountAt("/artists/artist-1");

    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1", {
      include: "tags",
    });
    expect(albumsApi.listAlbums).toHaveBeenCalledWith({
      q: "",
      artist_id: "artist-1",
      limit: 20,
      offset: 0,
      sort_by: "title",
      sort_dir: "asc",
    });
    expect(tracksApi.listTracks).toHaveBeenCalledWith({
      q: "",
      artist_id: "artist-1",
      limit: 20,
      offset: 0,
      include: "artist,album",
      sort_by: "created_at",
      sort_dir: "desc",
    });

    expect(wrapper.text()).toContain("The Larks");
    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).toContain("Song One");
  });

  it("shows aggregate stats in the header", async () => {
    vi.mocked(artistsApi.getArtistStats).mockResolvedValue({
      track_count: 42,
      album_count: 3,
    });

    await mountAt("/artists/artist-1");

    expect(artistsApi.getArtistStats).toHaveBeenCalledWith("artist-1");
    expect(wrapper.text()).toContain("3 albums");
    expect(wrapper.text()).toContain("42 tracks");
  });

  it("renders artist tags", async () => {
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks", ["rock", "indie"]),
    );

    await mountAt("/artists/artist-1");

    expect(wrapper.text()).toContain("rock");
    expect(wrapper.text()).toContain("indie");
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(artistsApi.getArtist).mockRejectedValue(new Error("not found"));

    await mountAt("/artists/artist-1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(albumsApi.listAlbums).mockResolvedValue([
      createAlbum("album-1", "Meadowland"),
    ]);
    vi.mocked(tracksApi.listTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("The Larks");
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

    await mountAt("/artists/artist-1");

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      artist_id: "artist-1",
      limit: 20,
      offset: 20,
      include: "artist,album",
      sort_by: "created_at",
      sort_dir: "desc",
    });
    expect(wrapper.text()).toContain("Song 19");
    expect(wrapper.text()).toContain("Song 20");
  });

  it("reloads on route param change", async () => {
    const router = createTestRouter();
    await router.push("/artists/artist-1");
    await router.isReady();
    wrapper = mount(ArtistView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-2", "Night Owls"),
    );
    await router.push("/artists/artist-2");
    await flushPromises();

    expect(artistsApi.getArtist).toHaveBeenLastCalledWith("artist-2", {
      include: "tags",
    });
    expect(wrapper.text()).toContain("Night Owls");
  });

  async function openMenu() {
    await wrapper.find(".entity-actions__more").trigger("click");
    await flushPromises();
    return Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    );
  }

  it("shows the edit action for an admin", async () => {
    setAdmin("admin-1");
    await mountAt("/artists/artist-1");

    const labels = (await openMenu()).map((el) => el.textContent?.trim());
    expect(labels).toContain(i18n.global.t("common.edit"));
  });

  it("hides the edit action for non-admin users", async () => {
    setAuthenticated("user-1");
    await mountAt("/artists/artist-1");

    const labels = (await openMenu()).map((el) => el.textContent?.trim());
    expect(labels).not.toContain(i18n.global.t("common.edit"));
  });

  describe("actions", () => {
    const t = i18n.global.t;

    async function selectMenuItem(label: string) {
      const item = (await openMenu()).find((el) =>
        el.textContent?.includes(label),
      );
      expect(item).toBeDefined();
      item!.dispatchEvent(new MouseEvent("click"));
      await flushPromises();
    }

    it("only shows Play and Share inline and collapses the rest", async () => {
      setAuthenticated("user-1");
      vi.mocked(tracksApi.listTracks).mockResolvedValue([
        createTrack("track-1", "Song One"),
      ]);
      await mountAt("/artists/artist-1");

      const inline = wrapper
        .findAll(".entity-actions__item")
        .map((b) => b.text());
      expect(inline).toEqual([t("common.play"), t("common.share")]);

      const labels = (await openMenu()).map((el) => el.textContent?.trim());
      expect(labels).toEqual(
        expect.arrayContaining([
          t("browse.contextMenu.enqueue"),
          t("common.download"),
          t("activities.view"),
          t("feeds.rss"),
          t("feeds.atom"),
        ]),
      );
    });

    it("plays all artist tracks", async () => {
      vi.mocked(tracksApi.listTracks).mockResolvedValue([
        createTrack("track-1", "Song One"),
        createTrack("track-2", "Song Two"),
      ]);
      await mountAt("/artists/artist-1");

      const playAll = vi.spyOn(usePlayerStore(), "playAll");
      const playButton = wrapper
        .findAll("button")
        .find((b) => b.text() === t("common.play"));
      expect(playButton).toBeDefined();

      await playButton?.trigger("click");
      await flushPromises();

      expect(playAll).toHaveBeenCalledOnce();
      expect(playAll.mock.calls[0]![0].map((track) => track.id)).toEqual([
        "track-1",
        "track-2",
      ]);
    });

    it("adds all artist tracks to the queue", async () => {
      vi.mocked(tracksApi.listTracks).mockResolvedValue([
        createTrack("track-1", "Song One"),
        createTrack("track-2", "Song Two"),
      ]);
      await mountAt("/artists/artist-1");

      const enqueue = vi.spyOn(usePlayerStore(), "enqueue");
      await selectMenuItem(t("browse.contextMenu.enqueue"));

      expect(enqueue).toHaveBeenCalledTimes(2);
      expect(enqueue.mock.calls.map((call) => call[0].id)).toEqual([
        "track-1",
        "track-2",
      ]);
    });
  });
});
