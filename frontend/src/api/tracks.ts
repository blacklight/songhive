import {
  getAuthHeader,
  ApiError,
  apiRequest,
  apiRequestWithHeaders,
} from "./client";
import { buildUrl } from "./config";
import type { components } from "./types";

export type TrackResponse = components["schemas"]["TrackResponse"];
export type TrackUpdate = components["schemas"]["TrackUpdate"];

export function listTracks(params?: {
  q?: string;
  artist_id?: string;
  album_id?: string;
  genre?: string;
  year_from?: number;
  year_to?: number;
  library_id?: string;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  favorited?: boolean;
}): Promise<TrackResponse[]> {
  return apiRequest<TrackResponse[]>("/tracks/", { query: params });
}

export interface ListTracksResult {
  tracks: TrackResponse[];
  offset: number;
  total: number;
}

export async function listTracksWithMeta(params?: {
  q?: string;
  artist_id?: string;
  album_id?: string;
  genre?: string;
  year_from?: number;
  year_to?: number;
  library_id?: string;
  limit?: number;
  offset?: number;
  include?: string;
  around_track_id?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  favorited?: boolean;
}): Promise<ListTracksResult> {
  const response = await apiRequestWithHeaders<TrackResponse[]>("/tracks/", {
    query: params,
  });
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    tracks: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}

export function getTrack(
  id: string,
  params?: { include?: string },
): Promise<TrackResponse> {
  if (params) {
    return apiRequest<TrackResponse>(`/tracks/${id}`, { query: params });
  }
  return apiRequest<TrackResponse>(`/tracks/${id}`);
}

export function updateTrack(
  id: string,
  body: TrackUpdate,
): Promise<TrackResponse> {
  return apiRequest<TrackResponse>(`/tracks/${id}`, { method: "PATCH", body });
}

export interface BulkTrackDeleteRequest {
  track_ids: string[];
}

export interface BulkTrackDeleteResponse {
  deleted: number;
  track_ids: string[];
}

export interface TrackEnrichResponse {
  track_id: string;
  enqueued: boolean;
}

export function enrichTrack(id: string): Promise<TrackEnrichResponse> {
  return apiRequest<TrackEnrichResponse>(`/tracks/${id}/enrich`, {
    method: "POST",
  });
}

export function deleteTrack(id: string): Promise<void> {
  return apiRequest<void>(`/tracks/${id}`, { method: "DELETE" });
}

export function uploadTrackImage(
  id: string,
  file: File,
): Promise<TrackResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<TrackResponse>(`/tracks/${id}/image`, {
    method: "POST",
    body,
  });
}

export function deleteTrackImage(id: string): Promise<TrackResponse> {
  return apiRequest<TrackResponse>(`/tracks/${id}/image`, {
    method: "DELETE",
  });
}

export function deleteTracks(
  trackIds: string[],
): Promise<BulkTrackDeleteResponse> {
  return apiRequest<BulkTrackDeleteResponse>("/tracks/bulk", {
    method: "DELETE",
    body: { track_ids: trackIds } as BulkTrackDeleteRequest,
  });
}

function parseContentDisposition(header: string): string | undefined {
  const quoted = header.match(/filename="([^"]+)"/);
  if (quoted) {
    return quoted[1].replace(/\\(.)/g, "$1");
  }

  const rfc5987 = header.match(/filename\*=([^']*)'[^']*'([^;]+)/i);
  if (rfc5987) {
    try {
      return decodeURIComponent(rfc5987[2]);
    } catch {
      return undefined;
    }
  }

  const unquoted = header.match(/filename=([^;]+)/);
  if (unquoted) {
    return unquoted[1].trim().replace(/\+$/, "").replace(/;$/, "");
  }

  return undefined;
}

/**
 * Download a track's backing audio file as an attachment.
 *
 * The track ``audio_url`` already resolves to the file download endpoint, so
 * this helper only adds the bearer token (when one is available), requests the
 * file as an attachment, and triggers a browser save via a temporary object
 * URL.  It raises ``ApiError`` when the server rejects the request.
 */
export async function downloadTrack(
  audioUrl: string,
  filename?: string,
): Promise<void> {
  const auth = getAuthHeader();
  const url = buildUrl(audioUrl, { disposition: "attachment" });
  const headers: Record<string, string> = {};
  if (auth) {
    headers.Authorization = auth;
  }

  const response = await fetch(url, { headers });
  if (!response.ok) {
    const text = await response.text();
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
    }
    throw await ApiError.fromResponse(response, parsed);
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);

  const link = document.createElement("a");
  link.href = objectUrl;

  const disposition = response.headers.get("content-disposition");
  if (!filename) {
    filename = disposition ? parseContentDisposition(disposition) : undefined;
  }
  if (filename) {
    link.download = filename;
  }

  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(objectUrl);
}
