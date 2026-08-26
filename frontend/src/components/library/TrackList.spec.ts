import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import type { UserResponse } from "@/api/users";
import type { TrackResponse } from "@/player/types";
import { toQueueTrack } from "@/player/enrich";
import * as librariesApi from "@/api/libraries";
import * as tracksApi from "@/api/tracks";
import TrackList from "./TrackList.vue";

vi.mock("@/api/libraries", () => ({
  listLibraries: vi.fn().mockResolvedValue([]),
  createLibrary: vi.fn(),
  addTracksToLibrary: vi.fn(),
  removeTracksFromLibrary: vi.fn(),
}));

vi.mock("@/api/playlists", () => ({
  listPlaylists: vi.fn().mockResolvedValue([]),
  createPlaylist: vi.fn(),
  addTracksToPlaylist: vi.fn(),
  removeTracksFromPlaylist: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  deleteTrack: vi.fn(),
  deleteTracks: vi.fn().mockResolvedValue({ deleted: 0, track_ids: [] }),
  downloadTrack: vi.fn().mockResolvedValue(undefined),
}));

const actionsLabel = i18n.global.t("browse.detail.actions");

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

function makeTrack(overrides: Partial<TrackResponse> = {}): TrackResponse {
  return {
    id: "track-1",
    title: "Song One",
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 1,
    duration: 185,
    visibility: "public" as const,
    ...overrides,
  };
}

function mountTrackList(
  props: Record<string, unknown> = {},
  providedRouter = createTestRouter(),
) {
  return {
    wrapper: mount(TrackList, {
      props: { tracks: [], ...props },
      attachTo: document.body,
      global: { plugins: [providedRouter] },
    }),
    router: providedRouter,
  };
}

function findMenuLabel(text: string) {
  const menu = document.body.querySelector(".context-menu");
  const labels = Array.from(
    menu?.querySelectorAll(".context-menu__label") ?? [],
  );
  return labels.find((el) => el.textContent === text);
}

async function clickMenuItem(text: string) {
  const label = findMenuLabel(text);
  const item = label?.parentElement as HTMLElement | null;
  if (item) {
    item.click();
    await flushPromises();
  }
}

describe("TrackList", () => {
  let wrapper: ReturnType<typeof mountTrackList>["wrapper"];

  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    const authStore = useAuthStore();
    authStore.$patch({
      accessToken: "token",
      expiresAt: Date.now() + 60000,
      user: { id: "user-1", username: "user" } as UserResponse,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("renders track metadata and uses context as the artist fallback", async () => {
    ({ wrapper } = mountTrackList({
      tracks: [makeTrack()],
      context: "Fallback Artist",
    }));
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("Fallback Artist");
    expect(wrapper.text()).toContain("3:05");
  });

  it("enriches tracks from the lookup map", async () => {
    const enrich = new Map([
      [
        "track-1",
        {
          artist_name: "Resolved Artist",
          album_title: "Resolved Album",
          artwork_url: "https://example.com/art.jpg",
        },
      ],
    ]);

    ({ wrapper } = mountTrackList({
      tracks: [makeTrack()],
      showArtwork: true,
      enrich,
    }));
    await flushPromises();

    expect(wrapper.text()).toContain("Resolved Artist");
    expect(wrapper.text()).toContain("Resolved Album");
    expect(wrapper.find(".track-list__artwork").attributes("src")).toBe(
      "https://example.com/art.jpg",
    );
  });

  it("plays a single track and updates the player store", async () => {
    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    ({ wrapper } = mountTrackList({ tracks }));
    await flushPromises();

    await wrapper.find(".track-list__title-btn").trigger("click");
    await flushPromises();

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1", "track-2"]);
    expect(player.currentTrack?.id).toBe("track-1");
    expect(wrapper.emitted("play")?.[0]).toEqual([0]);
  });

  it("plays all tracks when the header button is clicked", async () => {
    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.findAll("button").at(0)?.trigger("click");
    await flushPromises();

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1", "track-2"]);
    expect(wrapper.emitted("play-all")?.length).toBe(1);
  });

  it("emits toggle-favorite from the context menu", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("common.favorite"));

    expect(wrapper.emitted("toggle-favorite")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "" }),
    ]);
  });

  it("emits play-next and enqueues the track next", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.playNext"));

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1"]);
    expect(wrapper.emitted("play-next")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "Artist" }),
    ]);
  });

  it("emits share from the context menu", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("common.share"));

    expect(wrapper.emitted("share")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "Artist" }),
    ]);
  });

  it("downloads the track from the context menu", async () => {
    const tracks = [makeTrack({ audio_url: "/api/v1/files/f1/download" })];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("common.download"));
    await flushPromises();

    expect(tracksApi.downloadTrack).toHaveBeenCalledWith(
      tracks[0].audio_url,
      tracks[0].title,
    );
  });

  it("navigates to the artist and album from the context menu", async () => {
    const router = createTestRouter();
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }, router));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.goToArtist"));
    expect(router.currentRoute.value.path).toBe("/artists/artist-1");

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.goToAlbum"));
    expect(router.currentRoute.value.path).toBe("/albums/album-1");
  });

  it("renders artist and album as router links in the table", async () => {
    const tracks = [
      makeTrack({
        artist: { id: "artist-1", name: "Artist Name" },
        album: {
          id: "album-1",
          title: "Album Title",
          artist_id: "artist-1",
          visibility: "public",
        },
      }),
    ];
    ({ wrapper } = mountTrackList({ tracks }));
    await flushPromises();

    const links = wrapper.findAll(".track-list__link");
    expect(links.length).toBe(2);
    expect(links[0]!.attributes("href")).toBe("/artists/artist-1");
    expect(links[0]!.text()).toBe("Artist Name");
    expect(links[1]!.attributes("href")).toBe("/albums/album-1");
    expect(links[1]!.text()).toBe("Album Title");
  });

  it("falls back to plain text when artist and album ids are missing", async () => {
    const tracks = [
      makeTrack({
        artist_id: "",
        album_id: null,
        artist: { id: "artist-1", name: "Artist Name" },
        album: {
          id: "album-1",
          title: "Album Title",
          artist_id: "artist-1",
          visibility: "public",
        },
      }),
    ];
    ({ wrapper } = mountTrackList({ tracks }));
    await flushPromises();

    expect(wrapper.findAll(".track-list__link").length).toBe(0);
    expect(wrapper.text()).toContain("Artist Name");
    expect(wrapper.text()).toContain("Album Title");
  });

  it("renders the artist as a router link in the compact view", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("max-width"),
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    try {
      const tracks = [
        makeTrack({ artist: { id: "artist-1", name: "Artist Name" } }),
      ];
      ({ wrapper } = mountTrackList({ tracks }));
      await flushPromises();

      const artistLink = wrapper.find("a.track-list__compact-artist");
      expect(artistLink.exists()).toBe(true);
      expect(artistLink.attributes("href")).toBe("/artists/artist-1");
      expect(artistLink.text()).toBe("Artist Name");
    } finally {
      Object.defineProperty(window, "matchMedia", {
        writable: true,
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });

  it("updates the player queue when enqueue is selected", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.enqueue"));

    const player = usePlayerStore();
    expect(player.queue.map((t) => t.id)).toEqual(["track-1"]);
    expect(wrapper.emitted("enqueue")?.[0]).toEqual([
      toQueueTrack(tracks[0], { artist_name: "Artist" }),
    ]);
  });

  it("opens the add-to-library modal from the context menu", async () => {
    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.addToLibrary"));
    await flushPromises();

    expect(document.body.querySelector(".app-modal__overlay")).not.toBeNull();
    expect(document.body.textContent).toContain(
      i18n.global.t("browse.addToCollection.libraryTitle", {
        name: "Song One",
      }),
    );
  });

  it("removes a track from a library via the context menu", async () => {
    vi.mocked(librariesApi.removeTracksFromLibrary).mockResolvedValue({
      removed: 1,
      track_ids: ["track-1"],
    });

    const tracks = [makeTrack()];
    ({ wrapper } = mountTrackList({
      tracks,
      context: "Artist",
      removableFrom: {
        type: "library",
        id: "library-1",
        canRemove: true,
        name: "My Library",
      },
    }));
    await flushPromises();

    await wrapper.find(`[aria-label="${actionsLabel}"]`).trigger("click");
    await flushPromises();

    await clickMenuItem(i18n.global.t("browse.contextMenu.removeFromLibrary"));
    await flushPromises();

    const confirm = document.body.querySelector(".app-modal__overlay");
    expect(confirm).not.toBeNull();

    const removeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) =>
        b.textContent === i18n.global.t("browse.removeFromCollection.confirm"),
    );
    removeButton?.click();
    await flushPromises();

    expect(librariesApi.removeTracksFromLibrary).toHaveBeenCalledWith(
      "library-1",
      {
        track_ids: ["track-1"],
      },
    );
    expect(wrapper.emitted("removed")?.[0]).toEqual([["track-1"]]);
  });

  it("removes selected tracks in bulk", async () => {
    vi.mocked(librariesApi.removeTracksFromLibrary).mockResolvedValue({
      removed: 2,
      track_ids: ["track-1", "track-2"],
    });

    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    ({ wrapper } = mountTrackList({
      tracks,
      context: "Artist",
      removableFrom: {
        type: "library",
        id: "library-1",
        canRemove: true,
        name: "My Library",
      },
    }));
    await flushPromises();

    const bulkButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    expect(bulkButton).toBeDefined();
    await bulkButton?.trigger("click");
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    expect(checkboxes.length).toBeGreaterThanOrEqual(3);

    await checkboxes[0]?.setValue(true);
    await flushPromises();

    const removeSelected = wrapper
      .findAll("button")
      .find(
        (b) => b.text() === i18n.global.t("browse.bulkEdit.removeSelected"),
      );
    expect(removeSelected).toBeDefined();
    await removeSelected?.trigger("click");
    await flushPromises();

    const confirmButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) =>
        b.textContent === i18n.global.t("browse.removeFromCollection.confirm"),
    );
    confirmButton?.click();
    await flushPromises();

    expect(librariesApi.removeTracksFromLibrary).toHaveBeenCalledWith(
      "library-1",
      {
        track_ids: ["track-1", "track-2"],
      },
    );
    expect(wrapper.emitted("removed")?.[0]).toEqual([["track-1", "track-2"]]);
  });

  it("deletes selected tracks in bulk with a single API call", async () => {
    vi.mocked(tracksApi.deleteTracks).mockResolvedValue({
      deleted: 2,
      track_ids: ["track-1", "track-2"],
    });

    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    ({ wrapper } = mountTrackList({
      tracks,
      context: "Artist",
      deletable: true,
    }));
    await flushPromises();

    const bulkButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.bulkEdit.start"));
    expect(bulkButton).toBeDefined();
    await bulkButton?.trigger("click");
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    expect(checkboxes.length).toBeGreaterThanOrEqual(3);

    await checkboxes[0]?.setValue(true);
    await flushPromises();

    const deleteSelected = wrapper
      .findAll("button")
      .find(
        (b) => b.text() === i18n.global.t("browse.bulkEdit.deleteSelected"),
      );
    expect(deleteSelected).toBeDefined();
    await deleteSelected?.trigger("click");
    await flushPromises();

    const confirmButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    confirmButton?.click();
    await flushPromises();

    expect(tracksApi.deleteTracks).toHaveBeenCalledWith(["track-1", "track-2"]);
    expect(wrapper.emitted("removed")?.[0]).toEqual([["track-1", "track-2"]]);
  });

  it("highlights the currently playing track in the table", async () => {
    const tracks = [
      makeTrack(),
      makeTrack({ id: "track-2", title: "Song Two" }),
    ];
    const queueTracks = tracks.map((t) =>
      toQueueTrack(t, { artist_name: "Artist" }),
    );

    const player = usePlayerStore();
    player.playTrack(queueTracks[0]!, queueTracks);

    ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
    await flushPromises();

    const currentRows = wrapper.findAll(".track-list__row--current");
    expect(currentRows.length).toBe(1);
    expect(currentRows[0]!.text()).toContain("Song One");

    const playing = wrapper.find(".track-list__playing");
    expect(playing.exists()).toBe(true);
    expect(playing.classes()).toContain("track-list__playing--active");

    player.pause();
    await flushPromises();

    const paused = wrapper.find(".track-list__playing");
    expect(paused.exists()).toBe(true);
    expect(paused.classes()).not.toContain("track-list__playing--active");

    player.playTrack(queueTracks[1]!, queueTracks);
    await flushPromises();

    const currentRows2 = wrapper.findAll(".track-list__row--current");
    expect(currentRows2.length).toBe(1);
    expect(currentRows2[0]!.text()).toContain("Song Two");
  });

  it("highlights the currently playing track in the compact view", async () => {
    const originalMatchMedia = window.matchMedia;
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("max-width"),
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });

    try {
      const tracks = [
        makeTrack(),
        makeTrack({ id: "track-2", title: "Song Two" }),
      ];
      const queueTracks = tracks.map((t) =>
        toQueueTrack(t, { artist_name: "Artist" }),
      );

      const player = usePlayerStore();
      player.playTrack(queueTracks[0]!, queueTracks);

      ({ wrapper } = mountTrackList({ tracks, context: "Artist" }));
      await flushPromises();

      const currentItems = wrapper.findAll(
        ".track-list__compact-item--current",
      );
      expect(currentItems.length).toBe(1);
      expect(currentItems[0]!.text()).toContain("Song One");

      const playing = wrapper.find(".track-list__playing");
      expect(playing.exists()).toBe(true);
      expect(playing.classes()).toContain("track-list__playing--active");

      player.pause();
      await flushPromises();

      const paused = wrapper.find(".track-list__playing");
      expect(paused.exists()).toBe(true);
      expect(paused.classes()).not.toContain("track-list__playing--active");
    } finally {
      Object.defineProperty(window, "matchMedia", {
        writable: true,
        configurable: true,
        value: originalMatchMedia,
      });
    }
  });
});
