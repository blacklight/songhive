import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as tracksApi from "@/api/tracks";
import * as albumsApi from "@/api/albums";
import * as artistsApi from "@/api/artists";
import * as playlistsApi from "@/api/playlists";
import * as librariesApi from "@/api/libraries";
import EmbedPanel from "./EmbedPanel.vue";

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
  listTracks: vi.fn(),
}));

vi.mock("@/api/albums", () => ({
  getAlbumStats: vi.fn(),
}));

vi.mock("@/api/artists", () => ({
  getArtistStats: vi.fn(),
}));

vi.mock("@/api/playlists", () => ({
  getPlaylistStats: vi.fn(),
  listPlaylistTracks: vi.fn(),
}));

vi.mock("@/api/libraries", () => ({
  getLibraryStats: vi.fn(),
  listLibraryTracks: vi.fn(),
}));

function createTrack(overrides: Record<string, unknown> = {}) {
  return {
    id: "track-1",
    title: "Song One",
    artist_id: "artist-1",
    artist: { id: "artist-1", name: "The Larks" },
    album: null,
    duration: 185,
    audio_url: "/api/v1/files/f1/download",
    filename: "song-one.mp3",
    owner_id: "user-1",
    visibility: "public",
    ...overrides,
  };
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(EmbedPanel, {
    attachTo: document.body,
    props: {
      itemType: "track",
      itemId: "track-1",
      title: "Song One",
      isPublic: true,
      ...props,
    },
  });
}

function selectValue(): string {
  const select = document.body.querySelector(
    "select",
  ) as HTMLSelectElement | null;
  return select?.value ?? "";
}

async function selectFormat(value: string) {
  const select = document.body.querySelector("select") as HTMLSelectElement;
  select.value = value;
  select.dispatchEvent(new Event("change"));
  await flushPromises();
}

function optionValues(): string[] {
  return Array.from(document.body.querySelectorAll("select option")).map(
    (o) => (o as HTMLOptionElement).value,
  );
}

function codeText(): string {
  return document.body.querySelector(".embed-panel__code")?.textContent ?? "";
}

describe("EmbedPanel", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(tracksApi.getTrack).mockResolvedValue(createTrack() as never);
    vi.mocked(tracksApi.listTracks).mockResolvedValue([]);
    vi.mocked(albumsApi.getAlbumStats).mockResolvedValue({
      track_count: 3,
      total_duration: 600,
    });
    vi.mocked(artistsApi.getArtistStats).mockResolvedValue({
      track_count: 3,
      album_count: 1,
    });
    vi.mocked(playlistsApi.getPlaylistStats).mockResolvedValue({
      track_count: 3,
      total_duration: 600,
    });
    vi.mocked(librariesApi.getLibraryStats).mockResolvedValue({
      track_count: 3,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("defaults to the HTML audio embed for tracks", async () => {
    wrapper = mountPanel({ downloadUrl: "/api/v1/files/f1/download" });
    await flushPromises();

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album",
    });
    expect(selectValue()).toBe("audio");

    const code = codeText();
    expect(code).toContain("<audio");
    expect(code).toContain("The Larks - Song One");
    expect(code).toContain("http://localhost:3000/api/v1/files/f1/download");
    expect(code).toContain("http://localhost:3000/tracks/track-1");
  });

  it("builds the markdown link with artist and title", async () => {
    wrapper = mountPanel();
    await flushPromises();
    await selectFormat("markdown");

    expect(codeText()).toBe(
      "[The Larks - Song One](http://localhost:3000/tracks/track-1)",
    );
  });

  it("falls back to the filename in the markdown link", async () => {
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack({ title: "", artist: null }) as never,
    );
    wrapper = mountPanel({ title: "" });
    await flushPromises();
    await selectFormat("markdown");

    expect(codeText()).toBe(
      "[song-one.mp3](http://localhost:3000/tracks/track-1)",
    );
  });

  it("builds the script embed", async () => {
    wrapper = mountPanel();
    await flushPromises();
    await selectFormat("script");

    const code = codeText();
    expect(code).toContain('class="songhive-embed"');
    expect(code).toContain('data-type="track"');
    expect(code).toContain('data-id="track-1"');
    expect(code).toContain("http://localhost:3000/embed.js");
  });

  it("builds the iframe embed", async () => {
    wrapper = mountPanel();
    await flushPromises();
    await selectFormat("iframe");

    expect(codeText()).toContain(
      'src="http://localhost:3000/embed/track/track-1"',
    );
  });

  it("omits the audio option when the track has no playable URL", async () => {
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack({ audio_url: null }) as never,
    );
    wrapper = mountPanel();
    await flushPromises();

    expect(optionValues()).toEqual(["markdown", "script", "iframe"]);
  });

  it("offers the HTML track list for small collections", async () => {
    vi.mocked(albumsApi.getAlbumStats).mockResolvedValue({
      track_count: 2,
      total_duration: 400,
    });
    vi.mocked(tracksApi.listTracks).mockResolvedValue([
      createTrack() as never,
      createTrack({
        id: "track-2",
        title: "Song Two",
        audio_url: "/api/v1/files/f2/download",
      }) as never,
    ]);

    wrapper = mountPanel({
      itemType: "album",
      itemId: "album-1",
      title: "Meadowland",
    });
    await flushPromises();

    expect(albumsApi.getAlbumStats).toHaveBeenCalledWith("album-1");
    expect(optionValues()[0]).toBe("html");
    expect(selectValue()).toBe("html");
    await flushPromises();

    const code = codeText();
    expect(code).toContain('<div class="songhive-tracklist">');
    expect(code.match(/<audio /g)?.length).toBe(2);
    expect(code).toContain("Meadowland");
  });

  it("hides the HTML track list for collections of 250 tracks or more", async () => {
    vi.mocked(playlistsApi.getPlaylistStats).mockResolvedValue({
      track_count: 250,
      total_duration: 60000,
    });

    wrapper = mountPanel({
      itemType: "playlist",
      itemId: "pl-1",
      title: "Mix",
    });
    await flushPromises();

    expect(optionValues()).toEqual(["markdown", "script", "iframe"]);
  });

  it("uses the entity name in the markdown link for collections", async () => {
    wrapper = mountPanel({
      itemType: "library",
      itemId: "lib-1",
      title: "My Library",
    });
    await flushPromises();
    await selectFormat("markdown");

    expect(codeText()).toBe(
      "[My Library](http://localhost:3000/libraries/lib-1)",
    );
  });

  it("excludes non-public tracks from the HTML track list", async () => {
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([
      createTrack() as never,
      createTrack({
        id: "track-2",
        title: "Secret",
        visibility: "private",
      }) as never,
      createTrack({
        id: "track-3",
        title: "NoAudio",
        audio_url: null,
      }) as never,
    ]);

    wrapper = mountPanel({
      itemType: "library",
      itemId: "lib-1",
      title: "My Library",
    });
    await flushPromises();
    await flushPromises();

    const code = codeText();
    expect(code.match(/<audio /g)?.length).toBe(1);
    expect(code).not.toContain("Secret");
    expect(code).not.toContain("NoAudio");
  });

  it("shows a hint instead of the form for non-public items", async () => {
    wrapper = mountPanel({ isPublic: false });
    await flushPromises();

    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.embedNotPublic"),
    );
    expect(document.body.querySelector("select")).toBeNull();
    expect(tracksApi.getTrack).not.toHaveBeenCalled();
  });

  it("copies the generated snippet", async () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    wrapper = mountPanel();
    await flushPromises();

    const copyButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.copy"));
    await copyButton?.click();
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("<audio"));
  });
});
