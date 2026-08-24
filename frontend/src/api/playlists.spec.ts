import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listPlaylists,
  createPlaylist,
  getPlaylist,
  type PlaylistResponse,
  type PlaylistCreate,
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
    expect(apiRequest).toHaveBeenCalledWith("/playlists", { query: undefined });
    expect(result).toEqual([samplePlaylist]);
  });

  it("listPlaylists passes pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listPlaylists({ limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/playlists", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("createPlaylist posts with body and visibility query", async () => {
    apiRequest.mockResolvedValueOnce(samplePlaylist);
    const body: PlaylistCreate = { name: "New Playlist" };
    await createPlaylist(body, { visibility: "public" });
    expect(apiRequest).toHaveBeenCalledWith("/playlists", {
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
});
