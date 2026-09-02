import { getAuthHeader, ApiError, apiRequest } from "./client";
import { buildUrl, API_PREFIX } from "./config";
import { i18n } from "@/i18n";
import type { components } from "./types";

export type StoredFileResponse = components["schemas"]["StoredFileResponse"];
export type FileUploadResult = StoredFileResponse & { trackId?: string };
export type ExternalDuplicateWarning =
  components["schemas"]["ExternalDuplicateWarning"];

export interface BulkFileUploadResult {
  filename?: string;
  stored_file?: StoredFileResponse;
  track_id?: string;
  duplicate: boolean;
  error?: string;
  status?: string;
  external_duplicate?: ExternalDuplicateWarning | null;
}

function makeProgressHandler(
  onProgress: (percent: number) => void,
  knownSize: number,
) {
  return (event: ProgressEvent) => {
    const total =
      event.lengthComputable && event.total > 0 ? event.total : knownSize;
    if (total > 0) {
      const percent = Math.min(100, Math.round((event.loaded * 100) / total));
      onProgress(percent);
    }
  };
}

function cleanupSignalHandler(
  abortSignal: AbortSignal | undefined,
  handler: (() => void) | null,
) {
  if (abortSignal && handler) {
    try {
      abortSignal.removeEventListener("abort", handler);
    } catch {
      // ignore
    }
  }
}

export async function uploadFile(
  file: File,
  visibility: "private" | "local" | "public" = "public",
  onProgress?: (percent: number) => void,
  libraryId?: string,
  abortSignal?: AbortSignal,
): Promise<FileUploadResult> {
  const auth = getAuthHeader();
  if (!auth) {
    throw new ApiError("Not authenticated", 401);
  }

  const url = buildUrl(`${API_PREFIX}/files/upload`, {
    visibility,
    library_id: libraryId,
  });

  return new Promise((resolve, reject) => {
    if (abortSignal?.aborted) {
      reject(new ApiError(i18n.global.t("errors.uploadAborted"), 0));
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Authorization", auth);

    if (onProgress) {
      xhr.upload.onprogress = makeProgressHandler(onProgress, file.size);
    }

    let signalHandler: (() => void) | null = null;

    const finish = () => {
      cleanupSignalHandler(abortSignal, signalHandler);
      signalHandler = null;
    };

    xhr.onload = () => {
      finish();
      const text = xhr.responseText ?? "";
      let parsed: unknown = null;
      if (text) {
        try {
          parsed = JSON.parse(text);
        } catch {
          parsed = null;
        }
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        // fromResponse only reads status and statusText; a minimal cast avoids
        // constructing an unused Response body and the implied body-availability
        // check. This deviates from the contract's illustrative
        // `ApiError.fromResponse(xhr, parsed)` because fromResponse expects a
        // Response, not an XMLHttpRequest.
        const response = {
          status: xhr.status,
          statusText: xhr.statusText,
        } as Response;
        void ApiError.fromResponse(response, parsed).then(reject);
        return;
      }

      // The backend may return X-Duplicate: true with a canonical row when bytes
      // are deduplicated; in that case the upload can reach 100% near-instantly.
      // Audio files are also imported as tracks and the resulting track id is
      // returned in the X-Track-Id header.
      const trackId = xhr.getResponseHeader("X-Track-Id") ?? undefined;
      resolve({ ...(parsed as StoredFileResponse), trackId });
    };

    xhr.onerror = () => {
      finish();
      reject(new ApiError(i18n.global.t("errors.uploadFailed"), 0));
    };

    xhr.onabort = () => {
      finish();
      reject(new ApiError(i18n.global.t("errors.uploadAborted"), 0));
    };

    const body = new FormData();
    body.append("file", file);
    xhr.send(body);

    if (abortSignal) {
      if (abortSignal.aborted) {
        xhr.abort();
      } else {
        signalHandler = () => {
          xhr.abort();
        };
        abortSignal.addEventListener("abort", signalHandler);
      }
    }
  });
}

export async function bulkUploadFiles(
  files: File[],
  visibility: "private" | "local" | "public" = "public",
  onProgress?: (percent: number) => void,
  libraryId?: string,
  abortSignal?: AbortSignal,
): Promise<BulkFileUploadResult[]> {
  const auth = getAuthHeader();
  if (!auth) {
    throw new ApiError("Not authenticated", 401);
  }

  const url = buildUrl(`${API_PREFIX}/files/upload/bulk`, {
    visibility,
    library_id: libraryId,
  });

  const totalSize = files.reduce((sum, file) => sum + file.size, 0);

  return new Promise((resolve, reject) => {
    if (abortSignal?.aborted) {
      reject(new ApiError(i18n.global.t("errors.uploadAborted"), 0));
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Authorization", auth);

    if (onProgress) {
      xhr.upload.onprogress = makeProgressHandler(onProgress, totalSize);
    }

    let signalHandler: (() => void) | null = null;

    const finish = () => {
      cleanupSignalHandler(abortSignal, signalHandler);
      signalHandler = null;
    };

    xhr.onload = () => {
      finish();
      const text = xhr.responseText ?? "";
      let parsed: unknown = null;
      if (text) {
        try {
          parsed = JSON.parse(text);
        } catch {
          parsed = null;
        }
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        const response = {
          status: xhr.status,
          statusText: xhr.statusText,
        } as Response;
        void ApiError.fromResponse(response, parsed).then(reject);
        return;
      }

      resolve(parsed as BulkFileUploadResult[]);
    };

    xhr.onerror = () => {
      finish();
      reject(new ApiError(i18n.global.t("errors.uploadFailed"), 0));
    };

    xhr.onabort = () => {
      finish();
      reject(new ApiError(i18n.global.t("errors.uploadAborted"), 0));
    };

    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    xhr.send(body);

    if (abortSignal) {
      if (abortSignal.aborted) {
        xhr.abort();
      } else {
        signalHandler = () => {
          xhr.abort();
        };
        abortSignal.addEventListener("abort", signalHandler);
      }
    }
  });
}

export function listFiles(params?: {
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<StoredFileResponse[]> {
  return apiRequest<StoredFileResponse[]>("/files/", { query: params });
}

export function getFile(fileId: string): Promise<StoredFileResponse> {
  return apiRequest<StoredFileResponse>(`/files/${fileId}`);
}

export function deleteFile(fileId: string): Promise<void> {
  return apiRequest<void>(`/files/${fileId}`, { method: "DELETE" });
}
