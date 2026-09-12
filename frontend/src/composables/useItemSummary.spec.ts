import { describe, it, expect, vi, beforeEach } from "vitest";
import { getItemSummary } from "./useItemSummary";
import { getTrack } from "@/api/tracks";
import { getAlbum } from "@/api/albums";
import type { TrackResponse } from "@/api/tracks";

vi.mock("@/api/tracks", () => ({ getTrack: vi.fn() }));
vi.mock("@/api/albums", () => ({ getAlbum: vi.fn() }));
vi.mock("@/api/artists", () => ({ getArtist: vi.fn() }));
vi.mock("@/api/files", () => ({ getFile: vi.fn() }));
vi.mock("@/api/libraries", () => ({ getLibrary: vi.fn() }));
vi.mock("@/api/playlists", () => ({ getPlaylist: vi.fn() }));
vi.mock("@/api/radios", () => ({ getRadio: vi.fn() }));

const getTrackMock = vi.mocked(getTrack);
const getAlbumMock = vi.mocked(getAlbum);

describe("useItemSummary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("maps track responses to a title and image", async () => {
    getTrackMock.mockResolvedValue({
      title: "Song",
      image_url: "/api/v1/files/img-1/download",
    } as TrackResponse);

    const summary = await getItemSummary("track", "t-1");
    expect(summary).toEqual({
      title: "Song",
      imageUrl: "/api/v1/files/img-1/download",
    });
  });

  it("deduplicates concurrent requests for the same item", async () => {
    getAlbumMock.mockResolvedValue({
      title: "Album",
      cover_url: "/api/v1/files/cov-1/download",
    } as never);

    const [a, b] = await Promise.all([
      getItemSummary("album", "alb-1"),
      getItemSummary("album", "alb-1"),
    ]);
    expect(getAlbumMock).toHaveBeenCalledTimes(1);
    expect(a).toEqual(b);
  });

  it("resolves null for unknown item types without calling an API", async () => {
    const summary = await getItemSummary("bogus", "x");
    expect(summary).toBeNull();
    expect(getTrackMock).not.toHaveBeenCalled();
  });

  it("resolves null when the fetch fails", async () => {
    getTrackMock.mockRejectedValue(new Error("404"));
    const summary = await getItemSummary("track", "missing");
    expect(summary).toBeNull();
  });
});
