import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import * as albumsApi from "@/api/albums";
import * as artistsApi from "@/api/artists";
import * as tracksApi from "@/api/tracks";
import type { AlbumResponse } from "@/api/albums";
import type { ArtistResponse } from "@/api/artists";
import type { TrackResponse } from "@/api/tracks";
import AlbumView from "./AlbumView.vue";

vi.mock("@/api/albums", () => ({
  getAlbum: vi.fn(),
  getAlbumStats: vi.fn(),
  deleteAlbum: vi.fn(),
  enrichAlbum: vi.fn(),
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
      { path: "/tags/:name", component: { template: "<div/>" } },
      { path: "/genres/:name", component: { template: "<div/>" } },
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createAlbum(
  id: string,
  title: string,
  tags: string[] = [],
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
    tags,
    genres: [],
    owner: {
      id: "user-1",
      username: "user-1",
      display_name: null,
      avatar_url: null,
    },
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
    tags: [],
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
    vi.mocked(albumsApi.getAlbumStats).mockResolvedValue({
      track_count: 0,
      total_duration: 0,
    });
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
      include: "tags,genres,owner",
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

  it("shows aggregate stats in the header", async () => {
    vi.mocked(albumsApi.getAlbumStats).mockResolvedValue({
      track_count: 12,
      total_duration: 3705,
    });

    await mountAt("/albums/album-1");

    expect(albumsApi.getAlbumStats).toHaveBeenCalledWith("album-1");
    expect(wrapper.text()).toContain("12 tracks");
    expect(wrapper.text()).toContain("1:01:45");
  });

  it("renders album tags", async () => {
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland", ["rock", "indie"]),
    );
    await mountAt("/albums/album-1");

    expect(wrapper.text()).toContain("rock");
    expect(wrapper.text()).toContain("indie");
  });

  it("does not render an empty tag section", async () => {
    await mountAt("/albums/album-1");

    expect(wrapper.text()).not.toContain(i18n.global.t("browse.detail.tags"));
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
      include: "tags,genres,owner",
    });
    expect(wrapper.text()).toContain("Sunset");
  });

  it("shows the edit action for an admin who is not the owner", async () => {
    setAdmin("admin-1");
    await mountAt("/albums/album-1");

    await wrapper.find(".entity-actions__more").trigger("click");
    await flushPromises();

    const labels = Array.from(
      document.body.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    ).map((el) => el.textContent?.trim());
    expect(labels).toContain(i18n.global.t("common.edit"));
  });

  describe("actions", () => {
    const t = i18n.global.t;

    async function openMenu() {
      await wrapper.find(".entity-actions__more").trigger("click");
      await flushPromises();
      return Array.from(
        document.body.querySelectorAll<HTMLElement>('[role="menuitem"]'),
      );
    }

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
      await mountAt("/albums/album-1");

      const inline = wrapper
        .findAll(".entity-actions__item")
        .map((b) => b.text());
      expect(inline).toEqual([t("common.play"), t("common.share")]);

      const labels = (await openMenu()).map((el) => el.textContent?.trim());
      expect(labels).toEqual(
        expect.arrayContaining([
          t("browse.contextMenu.enqueue"),
          t("common.download"),
          t("browse.enrich.metadata"),
          t("activities.view"),
          t("common.edit"),
          t("feeds.rss"),
          t("feeds.atom"),
          t("common.delete"),
        ]),
      );
    });

    it("plays all album tracks", async () => {
      vi.mocked(tracksApi.listTracks).mockResolvedValue([
        createTrack("track-1", "Song One"),
        createTrack("track-2", "Song Two"),
      ]);
      await mountAt("/albums/album-1");

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

    it("adds all album tracks to the queue", async () => {
      vi.mocked(tracksApi.listTracks).mockResolvedValue([
        createTrack("track-1", "Song One"),
        createTrack("track-2", "Song Two"),
      ]);
      await mountAt("/albums/album-1");

      const enqueue = vi.spyOn(usePlayerStore(), "enqueue");
      await selectMenuItem(t("browse.contextMenu.enqueue"));

      expect(enqueue).toHaveBeenCalledTimes(2);
      expect(enqueue.mock.calls.map((call) => call[0].id)).toEqual([
        "track-1",
        "track-2",
      ]);
    });

    it("opens the RSS feed from the menu", async () => {
      await mountAt("/albums/album-1");
      const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

      await selectMenuItem(t("feeds.rss"));

      expect(openSpy).toHaveBeenCalledWith(
        expect.stringContaining("/feeds/"),
        "_blank",
        "noopener",
      );
      openSpy.mockRestore();
    });
  });
});
