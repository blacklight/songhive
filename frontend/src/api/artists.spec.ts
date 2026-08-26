import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listArtists,
  getArtist,
  updateArtist,
  deleteArtist,
  uploadArtistImage,
  deleteArtistImage,
  uploadArtistCover,
  deleteArtistCover,
  type ArtistResponse,
  type ArtistUpdate,
} from "./artists";

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
  cover_url: null,
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

  it("getArtist passes include query param", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    await getArtist("a1", { include: "albums,tracks" });
    expect(apiRequest).toHaveBeenCalledWith("/artists/a1", {
      query: { include: "albums,tracks" },
    });
  });

  it("updateArtist patches with the provided body", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    const body: ArtistUpdate = { name: "New Name", bio: "New bio" };
    await updateArtist("a1", body);
    expect(apiRequest).toHaveBeenCalledWith("/artists/a1", {
      method: "PATCH",
      body,
    });
  });

  it("deleteArtist sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteArtist("a1");
    expect(apiRequest).toHaveBeenCalledWith("/artists/a1", {
      method: "DELETE",
      query: { recursive: false },
    });
  });

  it("uploadArtistImage posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    const file = new File([""], "image.jpg", { type: "image/jpeg" });
    await uploadArtistImage("a1", file);

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; body: FormData },
    ];
    expect(path).toBe("/artists/a1/image");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("deleteArtistImage sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    await deleteArtistImage("a1");
    expect(apiRequest).toHaveBeenCalledWith("/artists/a1/image", {
      method: "DELETE",
    });
  });

  it("uploadArtistCover posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    const file = new File([""], "cover.jpg", { type: "image/jpeg" });
    await uploadArtistCover("a1", file);

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; body: FormData },
    ];
    expect(path).toBe("/artists/a1/cover");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("deleteArtistCover sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(sampleArtist);
    await deleteArtistCover("a1");
    expect(apiRequest).toHaveBeenCalledWith("/artists/a1/cover", {
      method: "DELETE",
    });
  });
});
