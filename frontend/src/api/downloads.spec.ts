import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as client from "./client";
import {
  createArchive,
  listArchives,
  getArchive,
  deleteArchive,
  clearCompletedArchives,
  downloadArchiveFile,
  type DownloadArchive,
} from "./downloads";

vi.mock("./client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./client")>();
  return {
    ...original,
    apiRequest: vi.fn(),
  };
});

const apiRequest = vi.mocked(client.apiRequest);

function makeArchive(
  overrides: Partial<DownloadArchive> = {},
): DownloadArchive {
  return {
    id: "arch-1",
    status: "ready",
    label: "My Mix",
    item_count: 2,
    items: [
      { kind: "track", title: "Song", artist: "Artist" },
      { kind: "remote", title: "Remote", artist: "" },
    ],
    download_url: "/api/v1/downloads/arch-1/file",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("downloads api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("createArchive posts the selection", async () => {
    const archive = makeArchive();
    apiRequest.mockResolvedValue(archive);
    const request = { track_ids: ["t1", "t2"], label: "Mix" };

    const result = await createArchive(request);

    expect(apiRequest).toHaveBeenCalledWith("/downloads/", {
      method: "POST",
      body: request,
    });
    expect(result).toEqual(archive);
  });

  it("listArchives passes pagination as query params", async () => {
    apiRequest.mockResolvedValue([]);
    await listArchives({ limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/downloads/", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("getArchive fetches a single archive", async () => {
    apiRequest.mockResolvedValue(makeArchive());
    await getArchive("arch-1");
    expect(apiRequest).toHaveBeenCalledWith("/downloads/arch-1");
  });

  it("deleteArchive issues a DELETE", async () => {
    apiRequest.mockResolvedValue(undefined);
    await deleteArchive("arch-1");
    expect(apiRequest).toHaveBeenCalledWith("/downloads/arch-1", {
      method: "DELETE",
    });
  });

  it("clearCompletedArchives posts to the clear endpoint", async () => {
    apiRequest.mockResolvedValue({ cleared: 3 });
    const result = await clearCompletedArchives();
    expect(apiRequest).toHaveBeenCalledWith("/downloads/clear", {
      method: "POST",
    });
    expect(result.cleared).toBe(3);
  });
});

describe("downloadArchiveFile", () => {
  const clickMock = vi.fn();
  const createObjectURL = vi.fn(() => "blob:fake");
  const revokeObjectURL = vi.fn();

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(new Blob(["zip-bytes"]), {
            status: 200,
            headers: { "Content-Type": "application/zip" },
          }),
      ),
    );
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => {
      const el = document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        tag,
      ) as HTMLElement;
      if (tag === "a") (el as HTMLAnchorElement).click = clickMock;
      return el;
    }) as typeof document.createElement);
    clickMock.mockClear();
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("fetches the archive file and triggers a browser download", async () => {
    const archive = makeArchive();
    await downloadArchiveFile(archive);
    expect(fetch).toHaveBeenCalledWith(archive.download_url, {
      credentials: "same-origin",
    });
    expect(clickMock).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:fake");
  });

  it("rejects when the archive is not ready", async () => {
    await expect(
      downloadArchiveFile(
        makeArchive({ status: "pending", download_url: null }),
      ),
    ).rejects.toThrow("Archive is not ready");
    expect(fetch).not.toHaveBeenCalled();
  });
});
