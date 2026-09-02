import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { i18n } from "@/i18n";
import * as client from "./client";
import {
  uploadFile,
  bulkUploadFiles,
  getFile,
  type StoredFileResponse,
  type BulkFileUploadResult,
} from "./files";

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
  resolve?: () => void;
  reject?: () => void;
}

function createMockXHR(config: {
  status?: number;
  statusText?: string;
  responseText?: string;
  headers?: Record<string, string | null>;
  trigger?: "load" | "error" | "abort";
  progressSteps?: Array<{
    loaded: number;
    total: number;
    lengthComputable?: boolean;
  }>;
  manual?: boolean;
}): MockXhr {
  const {
    status = 200,
    statusText = "OK",
    responseText = "",
    trigger = "load",
    manual = false,
  } = config;

  const headers: Record<string, string | null> = config.headers ?? {};
  let done = false;

  function fireProgress() {
    const progressSteps = config.progressSteps ?? [{ loaded: 100, total: 100 }];
    for (const step of progressSteps) {
      if (xhr.upload.onprogress) {
        xhr.upload.onprogress({
          lengthComputable: step.lengthComputable ?? true,
          ...step,
        } as ProgressEvent);
      }
    }
  }

  function fire(event: "load" | "error" | "abort") {
    if (done) return;
    done = true;
    if (event === "load") {
      fireProgress();
      if (xhr.onload) xhr.onload();
    } else if (event === "error" && xhr.onerror) {
      xhr.onerror();
    } else if (event === "abort" && xhr.onabort) {
      xhr.onabort();
    }
  }

  const xhr: MockXhr = {
    open: vi.fn(),
    setRequestHeader: vi.fn(),
    send: vi.fn(() => {
      if (!manual) {
        queueMicrotask(() => fire(trigger));
      }
    }),
    abort: vi.fn(() => fire("abort")),
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
    resolve: () => fire("load"),
    reject: () => fire("error"),
  };

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

  it("reports progress using the file size when the browser has no total", async () => {
    mockXhr = createMockXHR({
      responseText: JSON.stringify(sampleFile),
      progressSteps: [
        { loaded: 2, total: 0, lengthComputable: false },
        { loaded: 6, total: 0, lengthComputable: false },
        { loaded: 8, total: 0, lengthComputable: false },
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

  it("calls xhr.abort and rejects when the signal is aborted", async () => {
    mockXhr = createMockXHR({ manual: true });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const controller = new AbortController();
    const promise = uploadFile(
      file,
      "public",
      undefined,
      undefined,
      controller.signal,
    );

    expect(mockXhr.send).toHaveBeenCalledTimes(1);
    controller.abort();

    await expect(promise).rejects.toMatchObject({
      status: 0,
      message: i18n.global.t("errors.uploadAborted"),
    });
    expect(mockXhr.abort).toHaveBeenCalledTimes(1);
  });

  it("rejects immediately when given an already-aborted signal", async () => {
    mockXhr = createMockXHR({
      responseText: JSON.stringify(sampleFile),
    });
    const stub = vi.fn(() => mockXhr);
    vi.stubGlobal("XMLHttpRequest", stub);

    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const controller = new AbortController();
    controller.abort();

    await expect(
      uploadFile(file, "public", undefined, undefined, controller.signal),
    ).rejects.toMatchObject({
      status: 0,
      message: i18n.global.t("errors.uploadAborted"),
    });
    expect(mockXhr.send).not.toHaveBeenCalled();
    expect(stub).not.toHaveBeenCalled();
  });
});

const sampleBulkResult: BulkFileUploadResult = {
  filename: "avatar.png",
  stored_file: sampleFile,
  track_id: undefined,
  duplicate: false,
  error: undefined,
};

describe("bulkUploadFiles", () => {
  let mockXhr: MockXhr;
  let originalXHR: typeof XMLHttpRequest;

  beforeEach(() => {
    client.setTokenProvider(() => "test-token");
    originalXHR = globalThis.XMLHttpRequest;
    mockXhr = createMockXHR({
      responseText: JSON.stringify([sampleBulkResult]),
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );
  });

  afterEach(() => {
    vi.stubGlobal("XMLHttpRequest", originalXHR);
  });

  it("posts multiple files to the bulk endpoint", async () => {
    const file1 = new File(["a"], "song1.mp3", { type: "audio/mpeg" });
    const file2 = new File(["b"], "song2.mp3", { type: "audio/mpeg" });
    const result = await bulkUploadFiles([file1, file2], "public");

    expect(mockXhr.open).toHaveBeenCalledWith(
      "POST",
      "/api/v1/files/upload/bulk?visibility=public",
    );
    expect(mockXhr.setRequestHeader).toHaveBeenCalledWith(
      "Authorization",
      "Bearer test-token",
    );
    expect(mockXhr.send).toHaveBeenCalledTimes(1);

    const [body] = mockXhr.send.mock.calls[0] as [FormData];
    expect(body).toBeInstanceOf(FormData);
    expect(body.getAll("files")).toEqual([file1, file2]);
    expect(result).toEqual([sampleBulkResult]);
  });

  it("includes visibility and library_id in the bulk URL", async () => {
    const file = new File(["a"], "song.mp3", { type: "audio/mpeg" });
    await bulkUploadFiles([file], "private", undefined, "lib1");

    expect(mockXhr.open).toHaveBeenCalledWith(
      "POST",
      "/api/v1/files/upload/bulk?visibility=private&library_id=lib1",
    );
  });

  it("reports upload progress via the optional callback", async () => {
    mockXhr = createMockXHR({
      responseText: JSON.stringify([sampleBulkResult]),
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
    await bulkUploadFiles([file], "public", onProgress);

    expect(onProgress).toHaveBeenCalledTimes(3);
    expect(onProgress).toHaveBeenNthCalledWith(1, 25);
    expect(onProgress).toHaveBeenNthCalledWith(2, 75);
    expect(onProgress).toHaveBeenNthCalledWith(3, 100);
  });

  it("reports progress using the combined file sizes when the browser has no total", async () => {
    mockXhr = createMockXHR({
      responseText: JSON.stringify([sampleBulkResult]),
      progressSteps: [
        { loaded: 1, total: 0, lengthComputable: false },
        { loaded: 4, total: 0, lengthComputable: false },
        { loaded: 5, total: 0, lengthComputable: false },
      ],
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file1 = new File(["abcd"], "a.txt", { type: "text/plain" });
    const file2 = new File(["x"], "b.txt", { type: "text/plain" });
    const onProgress = vi.fn();
    await bulkUploadFiles([file1, file2], "public", onProgress);

    expect(onProgress).toHaveBeenCalledTimes(3);
    expect(onProgress).toHaveBeenNthCalledWith(1, 20);
    expect(onProgress).toHaveBeenNthCalledWith(2, 80);
    expect(onProgress).toHaveBeenNthCalledWith(3, 100);
  });

  it("throws ApiError on a non-2xx response", async () => {
    mockXhr = createMockXHR({
      status: 429,
      statusText: "Too Many Requests",
      responseText: JSON.stringify({ detail: "Rate limit exceeded" }),
    });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(bulkUploadFiles([file])).rejects.toMatchObject({
      status: 429,
      detail: "Rate limit exceeded",
    });
  });

  it("rejects with ApiError when not authenticated", async () => {
    client.setTokenProvider(() => null);

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(bulkUploadFiles([file])).rejects.toMatchObject({
      status: 401,
    });
  });

  it("rejects with a localized ApiError on XHR error", async () => {
    mockXhr = createMockXHR({ trigger: "error" });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });

    await expect(bulkUploadFiles([file])).rejects.toMatchObject({
      status: 0,
      message: i18n.global.t("errors.uploadFailed"),
    });
  });

  it("calls xhr.abort and rejects when the signal is aborted", async () => {
    mockXhr = createMockXHR({ manual: true });
    vi.stubGlobal(
      "XMLHttpRequest",
      vi.fn(() => mockXhr),
    );

    const file = new File(["contents"], "avatar.png", { type: "image/png" });
    const controller = new AbortController();
    const promise = bulkUploadFiles(
      [file],
      "public",
      undefined,
      undefined,
      controller.signal,
    );

    expect(mockXhr.send).toHaveBeenCalledTimes(1);
    controller.abort();

    await expect(promise).rejects.toMatchObject({
      status: 0,
      message: i18n.global.t("errors.uploadAborted"),
    });
    expect(mockXhr.abort).toHaveBeenCalledTimes(1);
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
