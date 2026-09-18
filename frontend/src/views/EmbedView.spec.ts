import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as tracksApi from "@/api/tracks";
import * as albumsApi from "@/api/albums";
import * as artistsApi from "@/api/artists";
import * as playlistsApi from "@/api/playlists";
import * as librariesApi from "@/api/libraries";
import EmbedView from "./EmbedView.vue";

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
  listTracks: vi.fn(),
}));

vi.mock("@/api/albums", () => ({
  getAlbum: vi.fn(),
}));

vi.mock("@/api/artists", () => ({
  getArtist: vi.fn(),
}));

vi.mock("@/api/playlists", () => ({
  getPlaylist: vi.fn(),
  listPlaylistTracks: vi.fn(),
}));

vi.mock("@/api/libraries", () => ({
  getLibrary: vi.fn(),
  listLibraryTracks: vi.fn(),
}));

function createTrack(overrides: Record<string, unknown> = {}) {
  return {
    id: "track-1",
    title: "Song One",
    artist_id: "artist-1",
    artist: { id: "artist-1", name: "The Larks" },
    album: { id: "album-1", title: "Meadowland" },
    duration: 185,
    audio_url: "/api/v1/files/f1/download",
    image_url: null,
    owner_id: "user-1",
    visibility: "public",
    ...overrides,
  };
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/embed/:type(track|album|artist|playlist|library)/:id",
        name: "embed",
        component: EmbedView,
      },
    ],
  });
}

async function mountAt(path: string) {
  const router = createTestRouter();
  await router.push(path);
  const wrapper = mount(EmbedView, {
    attachTo: document.body,
    global: { plugins: [router] },
  });
  await flushPromises();
  return wrapper;
}

describe("EmbedView", () => {
  let wrapper: ReturnType<typeof mount> | undefined;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(tracksApi.getTrack).mockResolvedValue(createTrack() as never);
    vi.mocked(tracksApi.listTracks).mockResolvedValue([]);
    vi.mocked(albumsApi.getAlbum).mockResolvedValue({
      id: "album-1",
      title: "Meadowland",
      artist: { id: "artist-1", name: "The Larks" },
      cover_url: null,
      visibility: "public",
    } as never);
    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue({
      id: "pl-1",
      name: "Mix",
      image_url: null,
      cover_url: null,
      visibility: "public",
    } as never);
    vi.mocked(librariesApi.getLibrary).mockResolvedValue({
      id: "lib-1",
      name: "My Library",
      image_url: null,
      cover_url: null,
      visibility: "public",
    } as never);
    vi.mocked(artistsApi.getArtist).mockResolvedValue({
      id: "artist-1",
      name: "The Larks",
      image_url: null,
      cover_url: null,
    } as never);
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = undefined;
    document.body.innerHTML = "";
  });

  it("renders a compact player for a track", async () => {
    wrapper = await mountAt("/embed/track/track-1");

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album",
    });
    expect(document.body.textContent).toContain("Song One");
    expect(document.body.textContent).toContain("The Larks · Meadowland");

    const audio = document.body.querySelector(
      "audio",
    ) as HTMLAudioElement | null;
    expect(audio?.src).toBe("http://localhost:3000/api/v1/files/f1/download");

    const link = document.body.querySelector(
      ".embed__title",
    ) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(
      "http://localhost:3000/tracks/track-1",
    );
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("renders an expandable tracklist for an album", async () => {
    vi.mocked(tracksApi.listTracks).mockResolvedValue([
      createTrack({ track_number: 2, title: "Second" }) as never,
      createTrack({ track_number: 1, title: "First" }) as never,
      createTrack({
        id: "t3",
        title: "Hidden",
        visibility: "private",
      }) as never,
    ]);

    wrapper = await mountAt("/embed/album/album-1");

    expect(tracksApi.listTracks).toHaveBeenCalledWith(
      expect.objectContaining({ album_id: "album-1" }),
    );

    const rows = document.body.querySelectorAll(".embed__track-row");
    expect(rows.length).toBe(2);
    // Album tracks sort by track number.
    expect(rows[0].textContent).toContain("First");
    expect(rows[1].textContent).toContain("Second");
    expect(document.body.textContent).not.toContain("Hidden");

    // Expanding a row reveals its audio element.
    expect(document.body.querySelectorAll("audio").length).toBe(0);
    (rows[0] as HTMLElement).click();
    await flushPromises();
    const audio = document.body.querySelector(
      "audio",
    ) as HTMLAudioElement | null;
    expect(audio?.src).toContain("/api/v1/files/f1/download");
  });

  it("renders playlist tracks in order", async () => {
    vi.mocked(playlistsApi.listPlaylistTracks).mockResolvedValue([
      createTrack({ title: "A" }) as never,
      createTrack({ id: "t2", title: "B" }) as never,
    ]);

    wrapper = await mountAt("/embed/playlist/pl-1");

    expect(document.body.textContent).toContain("Mix");
    const rows = document.body.querySelectorAll(".embed__track-row");
    expect(rows.length).toBe(2);
  });

  it("shows an unavailable state when the entity cannot be loaded", async () => {
    vi.mocked(tracksApi.getTrack).mockRejectedValue(new Error("not found"));

    wrapper = await mountAt("/embed/track/track-1");

    expect(document.body.textContent).toContain(
      "This content is not available.",
    );
  });
});
