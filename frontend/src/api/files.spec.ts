import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { i18n } from "@/i18n";
import * as client from "./client";
import { uploadFile, getFile, type StoredFileResponse } from "./files";

vi.mock("./client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./client")>();
  return {
    ...original,
    apiRequest: vi.fn(),
  };
});

const apiRequest = vi.mocked(client.apiRequest);

interface MockXhr {
  open: ReturnType<typeof vi.fn>;
  setRequestHeader: ReturnType<typeof vi.fn>;
  send: ReturnType<typeof vi.fn>;
  abort: ReturnType<typeof vi.fn>;
  getResponseHeader: ReturnType<typeof vi.fn>;
  status: number;
  statusText: string;
  responseText: string;
  upload: { onprogress: ((event: ProgressEvent) => void) | null };
  onload: (() => void) | null;
  onerror: (() => void) | null;
  onabort: (() => void) | null;
}

function createMockXHR(config: {
  status?: number;
  statusText?: string;
  responseText?: string;
  headers?: Record<string, string | null>;
  trigger?: "load" | "error" | "abort";
  progressSteps?: Array<{ loaded: number; total: number }>;
}): MockXhr {
  const {
    status = 200,
    statusText = "OK",
    responseText = "",
    trigger = "load",
  } = config;

  const headers: Record<string, string | null> = config.headers ?? {};

  const xhr: MockXhr = {
    open: vi.fn(),
    setRequestHeader: vi.fn(),
    send: vi.fn(),
    abort: vi.fn(),
    getResponseHeader: vi.fn(
      (name: string) => headers[name.toLowerCase()] ?? null,
    ),
    status,
    statusText,
    responseText,
    upload: { onprogress: null },
    onload: null,
    onerror: null,
    onabort: null,
  };

  xhr.send = vi.fn(() => {
    queueMicrotask(() => {
      if (trigger === "load") {
        const progressSteps = config.progressSteps ?? [
          { loaded: 100, total: 100 },
        ];
        for (const step of progressSteps) {
          if (xhr.upload.onprogress) {
            xhr.upload.onprogress({
              lengthComputable: true,
              ...step,
            } as ProgressEvent);
          }
        }
        if (xhr.onload) xhr.onload();
      } else if (trigger === "error" && xhr.onerror) {
        xhr.onerror();
      } else if (trigger === "abort" && xhr.onabort) {
        xhr.onabort();
      }
    });
  });

  return xhr;
}

const sampleFile: StoredFileResponse = {
  id: "f1",
  content_type: "image/png",
  size: 8,
  sha256: "sha",
  owner_id: "u1",
  visibility: "public",
  original_filename: "avatar.png",
  url: "/api/v1/files/f1/download",
};

describe("uploadFile", () => {
  let mockXhr: MockXhr;
  let originalXHR: typeof XMLHttpRequest;

  beforeEach(() => {
    client.setTokenProvider(() => "test-token");
    originalXHR = globalThis.XMLHttpRequest;
    mockXhr = createMockXHR({
      responseText: JSON.stringify(sampleFile),
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );
  });

  afterEach(() => {
    vi.stubGlobal("XMLHttpRequest", originalXHR);
  });

  it("posts a public multipart upload with the auth header", async () => {
    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const result = await uploadFile(file, "public");

    expect(mockXhr.open).toHaveBeenCalledWith(
      "POST",
      "/api/v1/files/upload?visibility=public",
    );
    expect(mockXhr.setRequestHeader).toHaveBeenCalledWith(
      "Authorization",
      "Bearer test-token",
    );
    expect(mockXhr.send).toHaveBeenCalledTimes(1);

    const [body] = mockXhr.send.mock.calls[0] as [FormData];
    expect(body).toBeInstanceOf(FormData);
    expect(body.get("file")).toBe(file);

    expect(result.url).toBe("/api/v1/files/f1/download");
  });

  it("reports upload progress via the optional callback", async () => {
    mockXhr = createMockXHR({
      responseText: JSON.stringify(sampleFile),
      progressSteps: [
        { loaded: 25, total: 100 },
        { loaded: 75, total: 100 },
        { loaded: 100, total: 100 },
      ],
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const onProgress = vi.fn();
    await uploadFile(file, "public", onProgress);

    expect(onProgress).toHaveBeenCalledTimes(3);
    expect(onProgress).toHaveBeenNthCalledWith(1, 25);
    expect(onProgress).toHaveBeenNthCalledWith(2, 75);
    expect(onProgress).toHaveBeenNthCalledWith(3, 100);
  });

  it("defaults visibility to public", async () => {
    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    await uploadFile(file);

    expect(mockXhr.open).toHaveBeenCalledWith(
      "POST",
      "/api/v1/files/upload?visibility=public",
    );
  });

  it("includes the selected library_id in the upload URL", async () => {
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    await uploadFile(file, "private", undefined, "lib1");

    expect(mockXhr.open).toHaveBeenCalledWith(
      "POST",
      "/api/v1/files/upload?visibility=private&library_id=lib1",
    );
  });

  it("throws ApiError on a non-2xx response", async () => {
    mockXhr = createMockXHR({
      status: 400,
      statusText: "Bad Request",
      responseText: JSON.stringify({ detail: "Upload failed" }),
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(uploadFile(file)).rejects.toMatchObject({
      status: 400,
      detail: "Upload failed",
    });
  });

  it("rejects with ApiError when not authenticated", async () => {
    client.setTokenProvider(() => null);

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(uploadFile(file)).rejects.toMatchObject({
      status: 401,
    });
  });

  it("returns trackId from the X-Track-Id header", async () => {
    mockXhr = createMockXHR({
      responseText: JSON.stringify(sampleFile),
      headers: { "x-track-id": "t1" },
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const result = await uploadFile(file);

    expect(result.trackId).toBe("t1");
  });

  it("rejects with a localized ApiError on XHR error", async () => {
    mockXhr = createMockXHR({ trigger: "error" });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(uploadFile(file)).rejects.toMatchObject({
      status: 0,
      message: i18n.global.t("errors.uploadFailed"),
    });
  });

  it("rejects with a localized ApiError on XHR abort", async () => {
    mockXhr = createMockXHR({ trigger: "abort" });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(uploadFile(file)).rejects.toMatchObject({
      status: 0,
      message: i18n.global.t("errors.uploadAborted"),
    });
  });
});

describe("getFile", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("fetches the file metadata endpoint", async () => {
    apiRequest.mockResolvedValueOnce(sampleFile);

    const result = await getFile("f1");
    expect(apiRequest).toHaveBeenCalledWith("/files/f1");
    expect(result).toEqual(sampleFile);
  });
});
