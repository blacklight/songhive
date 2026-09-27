import { ApiError, apiRequest } from "./client";
import { API_PREFIX, buildUrl } from "./config";

export type ArchiveStatus = "pending" | "processing" | "ready" | "failed";
export type ArchiveItemKind = "track" | "remote" | "episode";

export interface ArchiveItem {
  kind: ArchiveItemKind | string;
  title: string;
  artist: string;
}

export interface ArchiveItemError {
  ref: string;
  title: string;
  error: string;
}

export interface DownloadArchive {
  id: string;
  status: ArchiveStatus | string;
  label: string;
  item_count: number;
  items: ArchiveItem[];
  item_errors?: ArchiveItemError[] | null;
  error?: string | null;
  size?: number | null;
  download_url?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface ArchiveCreateRequest {
  track_ids?: string[];
  remote_object_ids?: string[];
  episode_ids?: string[];
  album_id?: string;
  artist_id?: string;
  playlist_id?: string;
  library_id?: string;
  label?: string;
}

export function createArchive(
  request: ArchiveCreateRequest,
): Promise<DownloadArchive> {
  return apiRequest<DownloadArchive>("/downloads/", {
    method: "POST",
    body: request,
  });
}

export function listArchives(params?: {
  limit?: number;
  offset?: number;
}): Promise<DownloadArchive[]> {
  return apiRequest<DownloadArchive[]>("/downloads/", { query: params });
}

export function getArchive(archiveId: string): Promise<DownloadArchive> {
  return apiRequest<DownloadArchive>(`/downloads/${archiveId}`);
}

export function deleteArchive(archiveId: string): Promise<void> {
  return apiRequest<void>(`/downloads/${archiveId}`, { method: "DELETE" });
}

export function clearCompletedArchives(): Promise<{ cleared: number }> {
  return apiRequest<{ cleared: number }>("/downloads/clear", {
    method: "POST",
  });
}

async function throwResponseError(response: Response): Promise<never> {
  let parsed: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }
  throw await ApiError.fromResponse(response, parsed);
}

/**
 * Fetch a ready archive's ZIP and trigger a browser download.
 *
 * Same-origin fetch carries the HttpOnly auth cookies automatically.
 */
export async function downloadArchiveFile(
  archive: DownloadArchive,
): Promise<void> {
  if (!archive.download_url) {
    throw new ApiError("Archive is not ready", 400);
  }
  const response = await fetch(buildUrl(archive.download_url), {
    credentials: "same-origin",
  });
  if (!response.ok) {
    await throwResponseError(response);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `${archive.label || "download"}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export type M3uAccess = "exclude" | "token";

function contentDispositionFilename(disposition: string): string | null {
  const encoded = /filename\*=(?:UTF-8''|utf-8'')([^;]+)/i.exec(disposition);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ""));
    } catch {
      return null;
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : null;
}

/**
 * Fetch an M3U export of a playlist or album and trigger a browser download.
 *
 * Same-origin fetch carries the HttpOnly auth cookies automatically.
 * ``access="token"`` embeds share tokens in the stream URLs of tracks that
 * are not anonymously playable (the server requires manage rights or a valid
 * container share token); ``"exclude"`` simply drops those entries.
 */
export async function downloadM3u(
  kind: "albums" | "playlists",
  id: string,
  options?: { access?: M3uAccess },
): Promise<void> {
  const response = await fetch(
    buildUrl(`${API_PREFIX}/${kind}/${id}/m3u`, {
      access: options?.access === "token" ? "token" : undefined,
    }),
    { credentials: "same-origin" },
  );
  if (!response.ok) {
    await throwResponseError(response);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download =
      contentDispositionFilename(
        response.headers.get("content-disposition") ?? "",
      ) ?? "export.m3u";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
