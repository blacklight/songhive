import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as librariesApi from "@/api/libraries";
import type { LibraryResponse } from "@/api/libraries";
import type { TrackResponse } from "@/api/tracks";
import LibraryDetailView from "./LibraryDetailView.vue";

vi.mock("@/api/libraries", () => ({
  getLibrary: vi.fn(),
  listLibraryTracks: vi.fn(),
  deleteLibrary: vi.fn(),
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
    can_write: true,
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

describe("LibraryDetailView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(librariesApi.getLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library"),
    );
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([]);
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
      include: "artist,album",
    });

    expect(wrapper.text()).toContain("Main Library");
    expect(wrapper.text()).toContain("Main music library.");
    expect(wrapper.text()).toContain("Song One");
    expect(
      wrapper.find(".library-detail-view__visibility i").classes(),
    ).toContain("fa-globe");
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
      include: "artist,album",
    });
    expect(wrapper.text()).toContain("Song 19");
    expect(wrapper.text()).toContain("Song 20");
  });

  it("displays artist and album metadata from included track summaries", async () => {
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);

    await mountAt("/libraries/library-1");
    await flushPromises();

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
