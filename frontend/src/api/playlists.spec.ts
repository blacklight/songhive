import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listPlaylists,
  createPlaylist,
  getPlaylist,
  updatePlaylist,
  deletePlaylist,
  uploadPlaylistImage,
  deletePlaylistImage,
  uploadPlaylistCover,
  deletePlaylistCover,
  playlistItemToQueueTrack,
  type PlaylistItemResponse,
  type PlaylistResponse,
  type PlaylistCreate,
  type PlaylistUpdate,
} from "./playlists";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const samplePlaylist: PlaylistResponse = {
  id: "p1",
  name: "Test Playlist",
  owner_id: "u1",
  description: null,
  visibility: "public",
};

describe("playlists api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listPlaylists fetches the playlists endpoint", async () => {
    apiRequest.mockResolvedValueOnce([samplePlaylist]);
    const result = await listPlaylists();
    expect(apiRequest).toHaveBeenCalledWith("/playlists/", {
      query: undefined,
    });
    expect(result).toEqual([samplePlaylist]);
  });

  it("listPlaylists passes pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listPlaylists({ limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/playlists/", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("createPlaylist posts with body and visibility query", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    const body: PlaylistCreate = { name: "New Playlist" };
    await createPlaylist(body, { visibility: "public" });
    expect(apiRequest).toHaveBeenCalledWith("/playlists/", {
      method: "POST",
      body,
      query: { visibility: "public" },
    });
  });

  it("getPlaylist fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    const result = await getPlaylist("p1");
    expect(apiRequest).toHaveBeenCalledWith("/playlists/p1");
    expect(result).toEqual(samplePlaylist);
  });

  it("getPlaylist passes include query param", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    await getPlaylist("p1", { include: "owner" });
    expect(apiRequest).toHaveBeenCalledWith("/playlists/p1", {
      query: { include: "owner" },
    });
  });

  it("updatePlaylist patches with the provided body", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    const body: PlaylistUpdate = {
      name: "Updated",
      description: "New description",
      visibility: "local",
    };
    await updatePlaylist("p1", body);
    expect(apiRequest).toHaveBeenCalledWith("/playlists/p1", {
      method: "PATCH",
      body,
    });
  });

  it("deletePlaylist sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deletePlaylist("p1");
    expect(apiRequest).toHaveBeenCalledWith("/playlists/p1", {
      method: "DELETE",
      query: { recursive: false },
    });
  });

  it("uploadPlaylistImage posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    const file = new File([""], "image.jpg", { type: "image/jpeg" });
    await uploadPlaylistImage("p1", file);

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; body: FormData },
    ];
    expect(path).toBe("/playlists/p1/image");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("deletePlaylistImage sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    await deletePlaylistImage("p1");
    expect(apiRequest).toHaveBeenCalledWith("/playlists/p1/image", {
      method: "DELETE",
    });
  });

  it("uploadPlaylistCover posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    const file = new File([""], "cover.jpg", { type: "image/jpeg" });
    await uploadPlaylistCover("p1", file);

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; body: FormData },
    ];
    expect(path).toBe("/playlists/p1/cover");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("deletePlaylistCover sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    await deletePlaylistCover("p1");
    expect(apiRequest).toHaveBeenCalledWith("/playlists/p1/cover", {
      method: "DELETE",
    });
  });
});

describe("playlistItemToQueueTrack", () => {
  it("derives the player display fields from the nested track response", () => {
    // QueueTrack's artist_name/album_title/artwork_url don't exist on a raw
    // TrackResponse — shuffle-play chunks fetched outside the visible list
    // must still map them from artist/album/image_url.
    const item: PlaylistItemResponse = {
      item_id: "item-1",
      position: 0,
      type: "track",
      track: {
        id: "t1",
        title: "Song One",
        artist_id: "a1",
        album_id: "al1",
        extra_artists: [],
        visibility: "public",
        tags: [],
        genres: [],
        in_collection: false,
        is_external: false,
        image_url: "https://example.com/art.jpg",
        artist: { id: "a1", name: "The Larks" },
        album: {
          id: "al1",
          title: "Meadowland",
          artist_id: "a1",
          visibility: "public",
        },
      },
      episode: null,
    };

    const track = playlistItemToQueueTrack(item);

    expect(track?.id).toBe("t1");
    expect(track?.artist_name).toBe("The Larks");
    expect(track?.album_title).toBe("Meadowland");
    expect(track?.artwork_url).toBe("https://example.com/art.jpg");
  });

  it("returns null for items without a playable payload", () => {
    const item: PlaylistItemResponse = {
      item_id: "item-1",
      position: 0,
      type: "track",
      track: null,
      episode: null,
    };
    expect(playlistItemToQueueTrack(item)).toBeNull();
  });
});
