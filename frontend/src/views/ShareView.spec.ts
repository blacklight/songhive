import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { ApiError } from "@/api/client";
import * as sharesApi from "@/api/shares";
import * as artistsApi from "@/api/artists";
import * as albumsApi from "@/api/albums";
import ShareView from "./ShareView.vue";

vi.mock("@/api/shares", () => ({
  resolveShareUrl: vi.fn(),
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
      { path: "/share/:token", component: ShareView },
      { path: "/login", component: { template: "<div/>" } },
    ],
  });
}

function createTrackPayload() {
  return {
    id: "track-1",
    title: "Song One",
    artist_id: "artist-1",
    album_id: "album-1",
    duration: 185,
    audio_url: "https://example.com/audio.mp3",
    owner_id: "user-1",
    visibility: "public",
  };
}

function createAlbumPayload() {
  return {
    id: "album-1",
    title: "Meadowland",
    artist_id: "artist-1",
    release_year: 2024,
    cover_url: null,
    owner_id: "user-1",
    visibility: "public",
  };
}

describe("ShareView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue(
      createTrackPayload(),
    );
    vi.mocked(artistsApi.getArtist).mockResolvedValue({
      id: "artist-1",
      name: "The Larks",
      musicbrainz_id: null,
      bio: null,
      image_file_id: null,
      image_url: null,
    });
    vi.mocked(albumsApi.getAlbum).mockResolvedValue({
      id: "album-1",
      title: "Meadowland",
      artist_id: "artist-1",
      musicbrainz_id: null,
      release_year: 2024,
      cover_url: null,
      description: null,
      owner_id: "user-1",
      visibility: "public",
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(token: string) {
    const router = createTestRouter();
    await router.push(`/share/${token}`);
    await router.isReady();
    wrapper = mount(ShareView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("renders a resolved track preview with artist and album names", async () => {
    await mountAt("abc");

    expect(sharesApi.resolveShareUrl).toHaveBeenCalledWith("abc");
    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1");

    expect(document.body.textContent).toContain("Song One");
    expect(document.body.textContent).toContain("The Larks");
    expect(document.body.textContent).toContain("Meadowland");
    expect(document.body.textContent).toContain("3:05");
    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.openInApp"),
    );
  });

  it("renders a resolved album preview", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue(
      createAlbumPayload(),
    );

    await mountAt("album-token");

    expect(document.body.textContent).toContain("Meadowland");
    expect(document.body.textContent).toContain("The Larks");
    expect(document.body.textContent).toContain("2024");
  });

  it("renders a nested { item_type, item } payload", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue({
      item_type: "playlist",
      item: { id: "playlist-1", name: "Mixtape", description: "Great tunes" },
    });

    await mountAt("playlist-token");

    expect(document.body.textContent).toContain("Mixtape");
    expect(document.body.textContent).toContain("Great tunes");
  });

  it("renders an expired share message", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockRejectedValue(
      new ApiError("Not found", 410),
    );

    await mountAt("expired");

    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.shareExpired"),
    );
    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.openInApp"),
    );
  });

  it("renders a revoked share message", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockRejectedValue(
      new ApiError("forbidden", 403),
    );

    await mountAt("revoked");

    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.shareRevoked"),
    );
  });

  it("renders a not found message", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockRejectedValue(
      new ApiError("Not found", 404),
    );

    await mountAt("missing");

    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.shareNotFound"),
    );
  });

  it("does not crash on an unexpected payload", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue({
      foo: "bar",
    });

    await mountAt("unknown");

    expect(document.body.textContent).toContain(
      i18n.global.t("errors.unknown"),
    );
    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.openInApp"),
    );
  });

  it("renders an unknown item title for a payload with only an id", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue({
      item_type: "track",
      item: { id: "track-2" },
    });

    await mountAt("titleless");

    expect(document.body.textContent).toContain(
      i18n.global.t("browse.share.unknownItem"),
    );
    expect(document.body.textContent).not.toContain("track-2");
  });

  it("reloads when the token route param changes", async () => {
    const router = createTestRouter();
    await router.push("/share/first");
    await router.isReady();

    wrapper = mount(ShareView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue({
      item_type: "track",
      item: { id: "track-2", title: "Song Two" },
    });

    await router.push("/share/second");
    await flushPromises();

    expect(sharesApi.resolveShareUrl).toHaveBeenLastCalledWith("second");
    expect(document.body.textContent).toContain("Song Two");
  });

  it("offers a public URL tab and copies it for public items", async () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    await mountAt("public-track");

    const publicTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.publicUrl"),
    );
    expect(publicTab).toBeDefined();

    await publicTab?.click();
    await flushPromises();

    const urlInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    expect(urlInput?.value).toBe("http://localhost:3000/tracks/track-1");

    const copyButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.copy"));
    await copyButton?.click();
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith(
      "http://localhost:3000/tracks/track-1",
    );
  });

  it("does not show a public URL tab for private items", async () => {
    vi.mocked(sharesApi.resolveShareUrl).mockResolvedValue({
      ...createTrackPayload(),
      visibility: "private",
    });

    await mountAt("private-track");

    const publicTab = Array.from(document.body.querySelectorAll("button")).find(
      (b) => b.textContent === i18n.global.t("browse.share.publicUrl"),
    );
    expect(publicTab).toBeUndefined();
  });
});
