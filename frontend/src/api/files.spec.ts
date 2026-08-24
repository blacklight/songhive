import { describe, it, expect, vi, beforeEach } from "vitest";
import { uploadFile, type StoredFileResponse } from "./files";
import { setTokenProvider } from "./client";

function createMockResponse(body: unknown, status = 200): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response;
}

describe("uploadFile", () => {
  beforeEach(() => {
    setTokenProvider(() => "test-token");
  });

  it("posts a public multipart upload with the auth header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      createMockResponse({
        id: "f1",
        content_type: "image/png",
        size: 8,
        sha256: "sha",
        owner_id: "u1",
        visibility: "public",
        original_filename: "avatar.png",
        url: "/api/v1/files/f1/download",
      } as StoredFileResponse),
    );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const result = await uploadFile(file, "public");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/files/upload?visibility=public");
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer test-token",
    );
    expect((init?.body as FormData).get("file")).toBe(file);

    expect(result.url).toBe("/api/v1/files/f1/download");
  });

  it("throws ApiError on a non-2xx response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      createMockResponse({ detail: "Upload failed" }, 400),
    );
    vi.stubGlobal("fetch", fetchMock);

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(uploadFile(file)).rejects.toMatchObject({
      status: 400,
      detail: "Upload failed",
    });
  });
});
