import { getAuthHeader, ApiError, apiRequest } from "./client";
import { buildUrl, API_PREFIX } from "./config";
import { i18n } from "@/i18n";
import type { components } from "./types";

export type StoredFileResponse = components["schemas"]["StoredFileResponse"];

export async function uploadFile(
  file: File,
  visibility: "private" | "local" | "public" = "public",
  onProgress?: (percent: number) => void,
): Promise<StoredFileResponse> {
  const auth = getAuthHeader();
  if (!auth) {
    throw new ApiError("Not authenticated", 401);
  }

  const url = buildUrl(`${API_PREFIX}/files/upload`, { visibility });

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Authorization", auth);

    if (onProgress) {
      xhr.upload.onprogress = (event: ProgressEvent) => {
        if (event.lengthComputable) {
          const percent = Math.round((event.loaded * 100) / event.total);
          onProgress(percent);
        }
      };
    }

    xhr.onload = () => {
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
      resolve(parsed as StoredFileResponse);
    };

    xhr.onerror = () => {
      reject(new ApiError(i18n.global.t("errors.uploadFailed"), 0));
    };

    xhr.onabort = () => {
      reject(new ApiError(i18n.global.t("errors.uploadAborted"), 0));
    };

    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
}

export function listFiles(): never {
  throw new Error("not implemented in Phase 1");
}

export function getFile(fileId: string): Promise<StoredFileResponse> {
  return apiRequest<StoredFileResponse>(`/files/${fileId}`);
}
