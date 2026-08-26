import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listAlbums,
  getAlbum,
  updateAlbum,
  deleteAlbum,
  uploadAlbumCover,
  deleteAlbumCover,
  type AlbumResponse,
  type AlbumUpdate,
} from "./albums";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleAlbum: AlbumResponse = {
  id: "al1",
  title: "Test Album",
  artist_id: "a1",
  musicbrainz_id: null,
  release_year: 2024,
  cover_url: null,
  description: null,
  owner_id: "u1",
  visibility: "public",
};

describe("albums api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listAlbums fetches the albums endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleAlbum]);
    const result = await listAlbums();
    expect(apiRequest).toHaveBeenCalledWith("/albums/", { query: undefined });
    expect(result).toEqual([sampleAlbum]);
  });

  it("listAlbums passes filter and pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listAlbums({
      q: "foo",
      artist_id: "a1",
      year_from: 2000,
      year_to: 2020,
      limit: 10,
      offset: 5,
    });
    expect(apiRequest).toHaveBeenCalledWith("/albums/", {
      query: {
        q: "foo",
        artist_id: "a1",
        year_from: 2000,
        year_to: 2020,
        limit: 10,
        offset: 5,
      },
    });
  });

  it("getAlbum fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleAlbum);
    const result = await getAlbum("al1");
    expect(apiRequest).toHaveBeenCalledWith("/albums/al1");
    expect(result).toEqual(sampleAlbum);
  });

  it("updateAlbum patches with the provided body", async () => {
    apiRequest.mockResolvedValueOnce(sampleAlbum);
    const body: AlbumUpdate = { title: "New Title", visibility: "public" };
    await updateAlbum("al1", body);
    expect(apiRequest).toHaveBeenCalledWith("/albums/al1", {
      method: "PATCH",
      body,
    });
  });

  it("deleteAlbum sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteAlbum("al1");
    expect(apiRequest).toHaveBeenCalledWith("/albums/al1", {
      method: "DELETE",
      query: { recursive: true },
    });
  });

  it("uploadAlbumCover posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce(sampleAlbum);
    const file = new File([""], "cover.jpg", { type: "image/jpeg" });
    await uploadAlbumCover("al1", file);

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; body: FormData },
    ];
    expect(path).toBe("/albums/al1/cover");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("deleteAlbumCover sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(sampleAlbum);
    await deleteAlbumCover("al1");
    expect(apiRequest).toHaveBeenCalledWith("/albums/al1/cover", {
      method: "DELETE",
    });
  });
});
