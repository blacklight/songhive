import { getAuthHeader, ApiError } from "./client";
import { buildUrl, API_PREFIX } from "./config";
import type { components } from "./types";

export type StoredFileResponse = components["schemas"]["StoredFileResponse"];

export async function uploadFile(
  file: File,
  visibility: "private" | "local" | "public" = "public",
): Promise<StoredFileResponse> {
  const auth = getAuthHeader();
  if (!auth) {
    throw new ApiError("Not authenticated", 401);
  }

  const url = buildUrl(`${API_PREFIX}/files/upload`, { visibility });
  const body = new FormData();
  body.append("file", file);

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: auth,
    },
    body,
  });

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    throw await ApiError.fromResponse(response, parsed);
  }

  return parsed as StoredFileResponse;
}

export function listFiles(): never {
  throw new Error("not implemented in Phase 1");
}

export function getFile(): never {
  throw new Error("not implemented in Phase 1");
}
