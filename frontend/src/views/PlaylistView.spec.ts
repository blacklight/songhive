import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as playlistsApi from "@/api/playlists";
import type {
  PlaylistEpisodeItem,
  PlaylistItemResponse,
  PlaylistResponse,
} from "@/api/playlists";
import type { TrackResponse } from "@/api/tracks";
import { useAuthStore } from "@/stores/auth";
import type { UserResponse } from "@/api/users";
import PlaylistView from "./PlaylistView.vue";

vi.mock("@/api/playlists", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playlists")>();
  return {
    getPlaylist: vi.fn(),
    getPlaylistStats: vi.fn(),
    listPlaylistItemsWithMeta: vi.fn(),
    playlistItemToQueueTrack: actual.playlistItemToQueueTrack,
    reorderPlaylistTracks: vi.fn(),
    deletePlaylist: vi.fn(),
  };
});

vi.mock("@/api/tracks", () => ({
  deleteTrack: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/playlists/:id", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/podcasts/:id", component: { template: "<div/>" } },
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createPlaylist(id: string, name: string): PlaylistResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: "A mix for the highway.",
    visibility: "public",
    owner: {
      id: "user-1",
      username: "user-1",
      display_name: null,
      avatar_url: null,
    },
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

function createItemsResult(
  items: PlaylistItemResponse[],
  total?: number,
): playlistsApi.ListPlaylistItemsResult {
  return {
    items,
    offset: 0,
    total: total ?? items.length,
  };
}

function createTrackItem(id: string, title: string): PlaylistItemResponse {
  return {
    item_id: `item-${id}`,
    position: 0,
    type: "track",
    track: createTrack(id, title),
    episode: null,
  };
}

function createEpisodeItem(id: string, title: string): PlaylistItemResponse {
  const episode: PlaylistEpisodeItem = {
    id,
    podcast_id: "pod-1",
    podcast_title: "The Show",
    title,
    description: null,
    link: null,
    image_url: null,
    audio_url: `https://example.com/${id}.mp3`,
    audio_type: "audio/mpeg",
    audio_length: 1000,
    duration_seconds: 600,
    published_at: "2026-01-10T00:00:00Z",
    season_number: null,
    episode_number: null,
    episode_type: null,
    played: false,
  };
  return {
    item_id: `item-${id}`,
    position: 0,
    type: "episode",
    track: null,
    episode,
  };
}

describe("PlaylistView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    const authStore = useAuthStore();
    authStore.$patch({
      user: { id: "user-1", username: "user" } as UserResponse,
    });
    vi.clearAllMocks();
    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue(
      createPlaylist("playlist-1", "Road Trip"),
    );
    vi.mocked(playlistsApi.getPlaylistStats).mockResolvedValue({
      track_count: 0,
      total_duration: 0,
    });
    vi.mocked(playlistsApi.listPlaylistItemsWithMeta).mockResolvedValue(
      createItemsResult([]),
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
    wrapper = mount(PlaylistView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("loads playlist and tracks on mount", async () => {
    vi.mocked(playlistsApi.listPlaylistItemsWithMeta).mockResolvedValue(
      createItemsResult([createTrackItem("track-1", "Song One")]),
    );

    await mountAt("/playlists/playlist-1");

    expect(playlistsApi.getPlaylist).toHaveBeenCalledWith("playlist-1", {
      include: "owner",
    });
    expect(playlistsApi.listPlaylistItemsWithMeta).toHaveBeenCalledWith(
      "playlist-1",
      {
        q: "",
        limit: 20,
        offset: 0,
        include: "artist,album",
        sort_by: "position",
        sort_dir: "asc",
      },
    );

    expect(wrapper.text()).toContain("Road Trip");
    expect(wrapper.text()).toContain("A mix for the highway.");
    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.find(".playlist-view__visibility i").classes()).toContain(
      "fa-globe",
    );
  });

  it("shows aggregate stats in the header", async () => {
    vi.mocked(playlistsApi.getPlaylistStats).mockResolvedValue({
      track_count: 8,
      total_duration: 1800,
    });

    await mountAt("/playlists/playlist-1");

    expect(playlistsApi.getPlaylistStats).toHaveBeenCalledWith("playlist-1");
    expect(wrapper.text()).toContain("8 tracks");
    expect(wrapper.text()).toContain("30:00");
  });

  it("shows an empty state when the playlist has no tracks", async () => {
    await mountAt("/playlists/playlist-1");

    expect(wrapper.text()).toContain("No tracks in this playlist yet.");
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(playlistsApi.getPlaylist).mockRejectedValue(
      new Error("not found"),
    );

    await mountAt("/playlists/playlist-1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue(
      createPlaylist("playlist-1", "Road Trip"),
    );
    vi.mocked(playlistsApi.listPlaylistItemsWithMeta).mockResolvedValue(
      createItemsResult([createTrackItem("track-1", "Song One")]),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Road Trip");
    expect(wrapper.text()).not.toContain("not found");
  });

  it("reloads on route param change", async () => {
    const router = createTestRouter();
    await router.push("/playlists/playlist-1");
    await router.isReady();
    wrapper = mount(PlaylistView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue(
      createPlaylist("playlist-2", "Chill"),
    );
    await router.push("/playlists/playlist-2");
    await flushPromises();

    expect(playlistsApi.getPlaylist).toHaveBeenLastCalledWith("playlist-2", {
      include: "owner",
    });
    expect(wrapper.text()).toContain("Chill");
  });

  it("filters tracks by search query", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(playlistsApi.listPlaylistItemsWithMeta)
        .mockResolvedValueOnce(
          createItemsResult([createTrackItem("track-1", "First Song")]),
        )
        .mockResolvedValueOnce(
          createItemsResult([createTrackItem("track-2", "Searched Song")]),
        );

      await mountAt("/playlists/playlist-1");

      const input = wrapper.find('input[type="search"]');
      expect(input.exists()).toBe(true);
      await input.setValue("searched");

      vi.advanceTimersByTime(0);
      vi.advanceTimersByTime(300);
      await flushPromises();

      expect(playlistsApi.listPlaylistItemsWithMeta).toHaveBeenLastCalledWith(
        "playlist-1",
        {
          q: "searched",
          limit: 20,
          offset: 0,
          include: "artist,album",
          sort_by: "position",
          sort_dir: "asc",
        },
      );
      expect(wrapper.text()).toContain("Searched Song");
      expect(wrapper.text()).not.toContain("First Song");
    } finally {
      vi.useRealTimers();
    }
  });

  it("reorders tracks and refreshes the list", async () => {
    vi.mocked(playlistsApi.listPlaylistItemsWithMeta).mockResolvedValue(
      createItemsResult([
        createTrackItem("track-1", "Song One"),
        createTrackItem("track-2", "Song Two"),
      ]),
    );
    vi.mocked(playlistsApi.reorderPlaylistTracks).mockResolvedValue({
      reordered: true,
      item_ids: ["track-2"],
      track_ids: ["track-2"],
      count: 1,
    });

    const router = createTestRouter();
    await router.push("/playlists/playlist-1");
    await router.isReady();
    wrapper = mount(PlaylistView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const startButton = wrapper
      .findAll("button")
      .find(
        (b) =>
          b.text() ===
          (wrapper as ReturnType<typeof mount>).vm.$t("browse.reorder.start"),
      );
    await startButton?.trigger("click");
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    await checkboxes[1]?.setValue(true);
    await flushPromises();

    const moveDownButtons = wrapper.findAll(
      '[aria-label="' +
        (wrapper as ReturnType<typeof mount>).vm.$t("browse.reorder.moveDown") +
        '"]',
    );
    await moveDownButtons[0]?.trigger("click");
    await flushPromises();

    expect(playlistsApi.reorderPlaylistTracks).toHaveBeenCalledWith(
      "playlist-1",
      { item_ids: ["track-1"], position: 2 },
    );
    expect(playlistsApi.listPlaylistItemsWithMeta).toHaveBeenCalledTimes(2);
  });

  it("renders podcast episodes alongside tracks", async () => {
    vi.mocked(playlistsApi.listPlaylistItemsWithMeta).mockResolvedValue(
      createItemsResult([
        createTrackItem("track-1", "Song One"),
        createEpisodeItem("ep-1", "Episode One"),
      ]),
    );

    await mountAt("/playlists/playlist-1");

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("Episode One");
    expect(wrapper.text()).toContain("The Show");
  });
});
