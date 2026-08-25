import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listLibraries,
  createLibrary,
  getLibrary,
  updateLibrary,
  deleteLibrary,
  listLibraryTracks,
  uploadTrack,
  bulkUploadTracks,
  scanLibrary,
  type LibraryResponse,
  type LibraryCreate,
  type LibraryUpdate,
  type ScanRequest,
} from "./libraries";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleLibrary: LibraryResponse = {
  id: "lib1",
  name: "Test Library",
  owner_id: "u1",
  description: null,
  visibility: "private",
  can_write: true,
};

describe("libraries api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listLibraries fetches the libraries endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleLibrary]);
    const result = await listLibraries();
    expect(apiRequest).toHaveBeenCalledWith("/libraries/", {
      query: undefined,
    });
    expect(result).toEqual([sampleLibrary]);
  });

  it("listLibraries passes pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listLibraries({ limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/libraries/", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("createLibrary posts with body and visibility query", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const body: LibraryCreate = { name: "New Library" };
    await createLibrary(body, { visibility: "public" });
    expect(apiRequest).toHaveBeenCalledWith("/libraries/", {
      method: "POST",
      body,
      query: { visibility: "public" },
    });
  });

  it("getLibrary fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const result = await getLibrary("lib1");
    expect(apiRequest).toHaveBeenCalledWith("/libraries/lib1");
    expect(result).toEqual(sampleLibrary);
  });

  it("updateLibrary patches with the provided body", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const body: LibraryUpdate = { name: "Updated", visibility: "local" };
    await updateLibrary("lib1", body);
    expect(apiRequest).toHaveBeenCalledWith("/libraries/lib1", {
      method: "PATCH",
      body,
    });
  });

  it("deleteLibrary sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteLibrary("lib1");
    expect(apiRequest).toHaveBeenCalledWith("/libraries/lib1", {
      method: "DELETE",
    });
  });

  it("listLibraryTracks fetches tracks for a library", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listLibraryTracks("lib1", { limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/libraries/lib1/tracks", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("uploadTrack posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce({});
    const file = new File([""], "track.mp3", { type: "audio/mpeg" });
    await uploadTrack("lib1", file, {
      force: true,
      visibility: "public",
      enrich: false,
    });

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; query?: unknown; body: FormData },
    ];
    expect(path).toBe("/libraries/lib1/tracks");
    expect(options.method).toBe("POST");
    expect(options.query).toEqual({
      force: true,
      visibility: "public",
      enrich: false,
    });
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("bulkUploadTracks posts a multipart FormData with the files field", async () => {
    apiRequest.mockResolvedValueOnce({});
    const file1 = new File([""], "track1.mp3", { type: "audio/mpeg" });
    const file2 = new File([""], "track2.mp3", { type: "audio/mpeg" });
    await bulkUploadTracks("lib1", [file1, file2], { force: false });

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; query?: unknown; body: FormData },
    ];
    expect(path).toBe("/libraries/lib1/tracks/bulk");
    expect(options.method).toBe("POST");
    expect(options.query).toEqual({ force: false });
    expect(options.body).toBeInstanceOf(FormData);
    const files = options.body.getAll("files");
    expect(files).toHaveLength(2);
    expect(files[0]).toBe(file1);
    expect(files[1]).toBe(file2);
  });

  it("scanLibrary posts a scan request", async () => {
    apiRequest.mockResolvedValueOnce({});
    const body: ScanRequest = { path: "/music" };
    await scanLibrary("lib1", body);
    expect(apiRequest).toHaveBeenCalledWith("/libraries/lib1/scan", {
      method: "POST",
      body,
    });
  });
});
