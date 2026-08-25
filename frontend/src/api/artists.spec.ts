import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import { listArtists, getArtist, type ArtistResponse } from "./artists";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleArtist: ArtistResponse = {
  id: "a1",
  name: "Test Artist",
  musicbrainz_id: null,
  bio: null,
  image_file_id: null,
  image_url: null,
};

describe("artists api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listArtists fetches the artists endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleArtist]);
    const result = await listArtists();
    expect(apiRequest).toHaveBeenCalledWith("/artists/", { query: undefined });
    expect(result).toEqual([sampleArtist]);
  });

  it("listArtists passes search and pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listArtists({ q: "foo", limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/artists/", {
      query: { q: "foo", limit: 10, offset: 5 },
    });
  });

  it("getArtist fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    const result = await getArtist("a1");
    expect(apiRequest).toHaveBeenCalledWith("/artists/a1");
    expect(result).toEqual(sampleArtist);
  });
});
