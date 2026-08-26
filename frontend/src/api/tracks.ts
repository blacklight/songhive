import { getAuthHeader, ApiError, apiRequest } from "./client";
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
}): Promise<TrackResponse[]> {
  return apiRequest<TrackResponse[]>("/tracks/", { query: params });
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
