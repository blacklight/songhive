import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as tracksApi from "@/api/tracks";
import * as artistsApi from "@/api/artists";
import * as albumsApi from "@/api/albums";
import * as favoritesApi from "@/api/favorites";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import type { TrackResponse } from "@/api/tracks";
import type { ArtistResponse } from "@/api/artists";
import type { AlbumResponse } from "@/api/albums";
import TrackView from "./TrackView.vue";

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
  deleteTrack: vi.fn(),
  downloadTrack: vi.fn(),
  enrichTrack: vi.fn(),
}));

vi.mock("@/api/favorites", () => ({
  addFavorite: vi.fn(),
  removeFavorite: vi.fn(),
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

function createTrack(
  id: string,
  title: string,
  tags: string[] = [],
  genres: string[] = [],
  image_url: string | null = null,
): TrackResponse {
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
    image_url,
    visibility: "public",
    owner_id: "user-1",
    tags,
    genres,
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

function createAlbum(
  id: string,
  title: string,
  cover_url: string | null = null,
): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    musicbrainz_id: null,
    release_year: 2024,
    cover_url,
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

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "tags,genres,owner",
    });
    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1");

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("The Larks");
    expect(wrapper.text()).toContain("Meadowland");
    expect(wrapper.text()).toContain("Indie");
    expect(wrapper.text()).toContain("3:05");
  });

  it("renders track tags", async () => {
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One", ["rock", "indie"]),
    );
    await mountAt("/tracks/track-1");

    expect(wrapper.text()).toContain("rock");
    expect(wrapper.text()).toContain("indie");
  });

  it("does not render an empty tag section", async () => {
    await mountAt("/tracks/track-1");

    expect(wrapper.text()).not.toContain(i18n.global.t("browse.detail.tags"));
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

    expect(tracksApi.getTrack).toHaveBeenLastCalledWith("track-2", {
      include: "tags,genres,owner",
    });
    expect(wrapper.text()).toContain("Song Two");
  });

  it("renders the track image when one is present", async () => {
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack(
        "track-1",
        "Song One",
        [],
        [],
        "https://example.com/track-image.jpg",
      ),
    );
    await mountAt("/tracks/track-1");

    const img = wrapper.find("img.track-view__cover");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("https://example.com/track-image.jpg");
  });

  it("falls back to the album cover when the track has no image", async () => {
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum(
        "album-1",
        "Meadowland",
        "https://example.com/album-cover.jpg",
      ),
    );
    await mountAt("/tracks/track-1");

    const img = wrapper.find("img.track-view__cover");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toBe("https://example.com/album-cover.jpg");
  });

  it("renders a placeholder when no cover is available", async () => {
    await mountAt("/tracks/track-1");

    expect(wrapper.find("img.track-view__cover").exists()).toBe(false);
    expect(wrapper.find(".track-view__cover").exists()).toBe(true);
  });

  describe("actions", () => {
    const t = i18n.global.t;

    function signIn() {
      useAuthStore().user = { id: "user-1", username: "user-1" } as never;
    }

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
      signIn();
      await mountAt("/tracks/track-1");

      const inline = wrapper
        .findAll(".entity-actions__item")
        .map((b) => b.text());
      expect(inline).toEqual([t("common.play"), t("common.share")]);

      const labels = (await openMenu()).map((el) => el.textContent?.trim());
      expect(labels).toEqual(
        expect.arrayContaining([
          t("browse.contextMenu.enqueue"),
          t("common.download"),
          t("common.favorite"),
          t("browse.contextMenu.enrich"),
          t("activities.view"),
          t("common.edit"),
          t("feeds.rss"),
          t("feeds.atom"),
          t("common.delete"),
        ]),
      );
    });

    it("adds the track to the queue", async () => {
      await mountAt("/tracks/track-1");
      const enqueue = vi.spyOn(player, "enqueue");

      await selectMenuItem(t("browse.contextMenu.enqueue"));

      expect(enqueue).toHaveBeenCalledOnce();
      expect(enqueue.mock.calls[0]![0].id).toBe("track-1");
    });

    it("downloads the track audio", async () => {
      await mountAt("/tracks/track-1");

      await selectMenuItem(t("common.download"));

      expect(tracksApi.downloadTrack).toHaveBeenCalledWith(
        "https://example.com/audio.mp3",
        "Song One",
      );
    });

    it("toggles the favorite state", async () => {
      signIn();
      await mountAt("/tracks/track-1");

      await selectMenuItem(t("common.favorite"));
      expect(favoritesApi.addFavorite).toHaveBeenCalledWith("track-1");

      await selectMenuItem(t("common.unfavorite"));
      expect(favoritesApi.removeFavorite).toHaveBeenCalledWith("track-1");
    });

    it("fetches metadata for managed tracks", async () => {
      signIn();
      await mountAt("/tracks/track-1");

      await selectMenuItem(t("browse.contextMenu.enrich"));

      expect(tracksApi.enrichTrack).toHaveBeenCalledWith("track-1");
    });
  });
});
