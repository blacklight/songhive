import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as client from "./client";
import {
  listTracks,
  getTrack,
  updateTrack,
  deleteTrack,
  deleteTracks,
  downloadTrack,
  uploadTrackImage,
  deleteTrackImage,
  type TrackResponse,
  type TrackUpdate,
} from "./tracks";

vi.mock("./client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./client")>();
  return {
    ...original,
    apiRequest: vi.fn(),
  };
});

const apiRequest = vi.mocked(client.apiRequest);

const sampleTrack: TrackResponse = {
  id: "t1",
  title: "Test Track",
  artist_id: "a1",
  album_id: "al1",
  track_number: 1,
  disc_number: 1,
  duration: 120,
  genre: "Rock",
  audio_url: "/api/v1/stream/t1",
  owner_id: "u1",
  visibility: "public",
};

describe("tracks api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listTracks fetches the tracks endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleTrack]);
    const result = await listTracks();
    expect(apiRequest).toHaveBeenCalledWith("/tracks/", { query: undefined });
    expect(result).toEqual([sampleTrack]);
  });

  it("listTracks passes filter and pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listTracks({
      q: "foo",
      artist_id: "a1",
      album_id: "al1",
      genre: "Rock",
      year_from: 2000,
      year_to: 2020,
      library_id: "lib1",
      limit: 10,
      offset: 5,
    });
    expect(apiRequest).toHaveBeenCalledWith("/tracks/", {
      query: {
        q: "foo",
        artist_id: "a1",
        album_id: "al1",
        genre: "Rock",
        year_from: 2000,
        year_to: 2020,
        library_id: "lib1",
        limit: 10,
        offset: 5,
      },
    });
  });

  it("getTrack fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleTrack);
    const result = await getTrack("t1");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1");
    expect(result).toEqual(sampleTrack);
  });

  it("updateTrack patches with the provided body", async () => {
    apiRequest.mockResolvedValueOnce(sampleTrack);
    const body: TrackUpdate = { title: "New Title", visibility: "local" };
    await updateTrack("t1", body);
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1", {
      method: "PATCH",
      body,
    });
  });

  it("deleteTrack sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteTrack("t1");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1", {
      method: "DELETE",
    });
  });

  it("deleteTracks sends a single DELETE request for multiple ids", async () => {
    const response = { deleted: 2, track_ids: ["t1", "t2"] };
    apiRequest.mockResolvedValueOnce(response);
    const result = await deleteTracks(["t1", "t2"]);
    expect(apiRequest).toHaveBeenCalledWith("/tracks/bulk", {
      method: "DELETE",
      body: { track_ids: ["t1", "t2"] },
    });
    expect(result).toEqual(response);
  });

  it("uploadTrackImage posts a multipart FormData with the file field", async () => {
    apiRequest.mockResolvedValueOnce(sampleTrack);
    const file = new File([""], "image.jpg", { type: "image/jpeg" });
    await uploadTrackImage("t1", file);

    const [path, options] = apiRequest.mock.calls[0] as [
      string,
      { method: string; body: FormData },
    ];
    expect(path).toBe("/tracks/t1/image");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get("file")).toBe(file);
  });

  it("deleteTrackImage sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(sampleTrack);
    await deleteTrackImage("t1");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1/image", {
      method: "DELETE",
    });
  });
});

describe("downloadTrack", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;
  let clickSpy: ReturnType<typeof vi.fn>;
  let link: HTMLAnchorElement;
  let originalFetch: typeof fetch;
  let originalCreateObjectURL: typeof URL.createObjectURL;
  let originalRevokeObjectURL: typeof URL.revokeObjectURL;

  beforeEach(() => {
    client.setTokenProvider(() => null);

    originalFetch = globalThis.fetch;
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    originalCreateObjectURL = globalThis.URL.createObjectURL;
    originalRevokeObjectURL = globalThis.URL.revokeObjectURL;
    createObjectURL = vi.fn().mockReturnValue("blob:mock");
    revokeObjectURL = vi.fn();
    Object.defineProperty(globalThis.URL, "createObjectURL", {
      value: createObjectURL,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", {
      value: revokeObjectURL,
      configurable: true,
      writable: true,
    });

    link = document.createElement("a");
    clickSpy = vi.fn();
    vi.spyOn(link, "click").mockImplementation(clickSpy);
    vi.spyOn(document, "createElement").mockReturnValue(link);
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    Object.defineProperty(globalThis.URL, "createObjectURL", {
      value: originalCreateObjectURL,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(globalThis.URL, "revokeObjectURL", {
      value: originalRevokeObjectURL,
      configurable: true,
      writable: true,
    });
    vi.restoreAllMocks();
  });

  function makeResponse(overrides: {
    ok?: boolean;
    status?: number;
    statusText?: string;
    blob?: Blob;
    contentDisposition?: string | null;
    bodyText?: string;
  }) {
    const blob = overrides.blob ?? new Blob(["audio"]);
    return {
      ok: overrides.ok ?? true,
      status: overrides.status ?? 200,
      statusText: overrides.statusText ?? "OK",
      headers: {
        get: (name: string) =>
          name.toLowerCase() === "content-disposition"
            ? (overrides.contentDisposition ?? null)
            : null,
      },
      blob: vi.fn().mockResolvedValue(blob),
      text: vi.fn().mockResolvedValue(overrides.bodyText ?? ""),
    };
  }

  it("fetches the audio file with an auth header and triggers a download", async () => {
    client.setTokenProvider(() => "token");
    fetchMock.mockResolvedValueOnce(
      makeResponse({
        contentDisposition: 'attachment; filename="song.mp3"',
      }),
    );

    await downloadTrack("/api/v1/files/f1/download");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/files/f1/download?disposition=attachment",
      { headers: { Authorization: "Bearer token" } },
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(link.href).toBe("blob:mock");
    expect(link.download).toBe("song.mp3");
    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("falls back to the supplied filename when there is no Content-Disposition", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({}));

    await downloadTrack("/api/v1/files/f1/download", "My Song");

    expect(link.download).toBe("My Song");
  });

  it("omits the Authorization header when the user is not authenticated", async () => {
    fetchMock.mockResolvedValueOnce(makeResponse({}));

    await downloadTrack("/api/v1/files/f1/download");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/files/f1/download?disposition=attachment",
      { headers: {} },
    );
  });

  it("throws an ApiError on a non-2xx response", async () => {
    fetchMock.mockResolvedValueOnce(
      makeResponse({
        ok: false,
        status: 403,
        statusText: "Forbidden",
        bodyText: JSON.stringify({ detail: "Access denied" }),
      }),
    );

    await expect(
      downloadTrack("/api/v1/files/f1/download"),
    ).rejects.toMatchObject({
      status: 403,
      detail: "Access denied",
    });
  });
});
