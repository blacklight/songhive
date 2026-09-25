import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as tracksApi from "@/api/tracks";
import * as remoteApi from "@/api/remote";
import type { TrackResponse, ListTracksResult } from "@/api/tracks";
import type { RemoteObject } from "@/api/remote";
import TracksView from "./TracksView.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";

vi.mock("@/api/tracks", () => ({
  listTracksWithMeta: vi.fn(),
  deleteTrack: vi.fn(),
}));

vi.mock("@/api/remote", () => ({
  listRemoteObjects: vi.fn(),
  listRemoteObjectsWithMeta: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/tracks/:id", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/remote/:kind/:id", component: { template: "<div/>" } },
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

function createRemoteTrack(
  id: string,
  name: string,
  artistName?: string,
): RemoteObject {
  return {
    id,
    canonical_url: `https://remote.example/tracks/${id}`,
    object_type: "Audio",
    resource_type: "track",
    domain: "remote.example",
    actor_url: "https://remote.example/users/alice",
    name,
    artist_name: artistName ?? null,
    audio_url: "https://remote.example/audio.mp3",
    stream_url: `/api/v1/remote/objects/${id}/stream`,
    visibility: "public",
    unavailable: false,
    url: `/remote/track/${id}`,
  };
}

function setAuthenticated() {
  const authStore = useAuthStore();
  authStore.status = "authenticated";
  authStore.user = { id: "user-1", username: "alice" } as never;
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
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [],
      offset: 0,
      total: 0,
    });
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

  it("keeps remote tracks in the list after loading the next page", async () => {
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
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [createRemoteTrack("ro-1", "Federated Song")],
      offset: 0,
      total: 1,
    });

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Federated Song");

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    await loadMore?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Song 20");
    expect(wrapper.text()).toContain("Federated Song");
  });

  it("keeps Load More alive on remote pages and fetches them in order", async () => {
    // Local list is exhausted after page 1 (1 of 1), but the remote
    // listing still has a second page — the merged list's Load More must
    // keep pulling remote pages.
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([createTrack("track-1", "Local Song")], 1),
    );
    vi.mocked(remoteApi.listRemoteObjectsWithMeta)
      .mockResolvedValueOnce({
        items: [createRemoteTrack("ro-1", "Federated Song")],
        offset: 0,
        total: 2,
      })
      .mockResolvedValueOnce({
        items: [createRemoteTrack("ro-2", "Federated Song Two")],
        offset: 1,
        total: 2,
      });

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({
        resource_type: "track",
        offset: 0,
        sort_by: "created_at",
        sort_dir: "desc",
      }),
    );

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 1 }),
    );
    expect(wrapper.text()).toContain("Federated Song");
    expect(wrapper.text()).toContain("Federated Song Two");
  });

  it("hides the collection toggle when signed out", async () => {
    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const toggle = wrapper.findComponent(CollectionToggle);
    expect(toggle.find('input[type="checkbox"]').exists()).toBe(false);
    expect(tracksApi.listTracksWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: undefined }),
    );
  });

  it("enables the collection filter by default when signed in", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(tracksApi.listTracksWithMeta);

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const checkbox = wrapper
      .findComponent(CollectionToggle)
      .find('input[type="checkbox"]');
    expect(checkbox.exists()).toBe(true);
    expect((checkbox.element as HTMLInputElement).checked).toBe(true);
    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: true }),
    );
  });

  it("drops the collection filter when the toggle is disabled", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(tracksApi.listTracksWithMeta);

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const checkbox = wrapper
      .findComponent(CollectionToggle)
      .find('input[type="checkbox"]');
    await checkbox.setValue(false);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: undefined }),
    );
  });

  it("renders remote tracks in the list with their domain badge", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([createTrack("track-1", "Local Song")]),
    );
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [createRemoteTrack("ro-1", "Federated Song", "Remote Artist")],
      offset: 0,
      total: 1,
    });

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenCalledWith(
      expect.objectContaining({ resource_type: "track" }),
    );
    expect(wrapper.text()).toContain("Local Song");
    expect(wrapper.text()).toContain("Federated Song");
    const badge = wrapper.find(".track-list__remote-badge");
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toContain("remote.example");
  });

  it("forwards the search query to the remote listing", async () => {
    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("anthem");

    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ resource_type: "track", q: "anthem" }),
    );
  });

  it("clusters a same-named remote track under the local one", async () => {
    vi.mocked(tracksApi.listTracksWithMeta).mockResolvedValue(
      createListResult([
        createTrack("track-1", "Twin", "The Artist"),
        createTrack("track-2", "Zebra", "The Artist"),
      ]),
    );
    vi.mocked(remoteApi.listRemoteObjectsWithMeta).mockResolvedValue({
      items: [createRemoteTrack("ro-1", "Twin", "The Artist")],
      offset: 0,
      total: 1,
    });

    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const titles = wrapper
      .findAll(".track-list__title-text")
      .map((el) => el.text());
    expect(titles).toEqual(["Twin", "Twin", "Zebra"]);
    const rows = wrapper.findAll("tr");
    const remoteRow = rows.find(
      (row) =>
        row.text().includes("Twin") &&
        row.find(".track-list__remote-badge").exists(),
    );
    expect(remoteRow).toBeDefined();
  });

  it("forwards the collection toggle to the remote listing", async () => {
    setAuthenticated();
    wrapper = mount(TracksView, {
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ resource_type: "track", collection: true }),
    );

    const checkbox = wrapper
      .findComponent(CollectionToggle)
      .find('input[type="checkbox"]');
    await checkbox.setValue(false);
    await flushPromises();

    expect(remoteApi.listRemoteObjectsWithMeta).toHaveBeenLastCalledWith(
      expect.objectContaining({ collection: undefined }),
    );
  });
});
