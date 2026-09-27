import type { components } from "./types";
import { apiRequest, apiRequestWithHeaders } from "./client";
import type { ProviderSyncStatus } from "./providerSync";

export type AlbumResponse = components["schemas"]["AlbumResponse"] & {
  /** Lazy-contents state for provider-backed albums (see Section 6). */
  provider_sync?: ProviderSyncStatus | null;
  /** Fields the provider allows editing locally (e.g. ["genres", "tags"]). */
  editable_fields?: string[] | null;
  /** Provider type backing the album (e.g. "tidal"); null when local. */
  external_provider_type?: string | null;
};
export type AlbumUpdate = components["schemas"]["AlbumUpdate"];

export function listAlbums(params?: {
  q?: string;
  artist_id?: string;
  year_from?: number;
  year_to?: number;
  genre?: string;
  owner_username?: string;
  collection?: boolean;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<AlbumResponse[]> {
  return apiRequest<AlbumResponse[]>("/albums/", { query: params });
}

export interface ListAlbumsResult {
  items: AlbumResponse[];
  offset: number;
  total: number;
}

export async function listAlbumsWithMeta(params?: {
  q?: string;
  artist_id?: string;
  year_from?: number;
  year_to?: number;
  genre?: string;
  owner_username?: string;
  collection?: boolean;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<ListAlbumsResult> {
  const response = await apiRequestWithHeaders<AlbumResponse[]>("/albums/", {
    query: params,
  });
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}

export function getAlbum(
  id: string,
  params?: { include?: string },
): Promise<AlbumResponse> {
  if (params) {
    return apiRequest<AlbumResponse>(`/albums/${id}`, { query: params });
  }
  return apiRequest<AlbumResponse>(`/albums/${id}`);
}

export interface AlbumStats {
  track_count: number;
  total_duration: number;
}

export function getAlbumStats(id: string): Promise<AlbumStats> {
  return apiRequest<AlbumStats>(`/albums/${id}/stats`);
}

export function updateAlbum(
  id: string,
  body: AlbumUpdate,
): Promise<AlbumResponse> {
  return apiRequest<AlbumResponse>(`/albums/${id}`, { method: "PATCH", body });
}

export interface AlbumEnrichResponse {
  album_id: string;
  enqueued: number;
}

export function deleteAlbum(id: string, recursive = true): Promise<void> {
  return apiRequest<void>(`/albums/${id}`, {
    method: "DELETE",
    query: { recursive },
  });
}

export function uploadAlbumCover(
  id: string,
  file: File,
): Promise<AlbumResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<AlbumResponse>(`/albums/${id}/cover`, {
    method: "POST",
    body,
  });
}

export function deleteAlbumCover(id: string): Promise<AlbumResponse> {
  return apiRequest<AlbumResponse>(`/albums/${id}/cover`, {
    method: "DELETE",
  });
}

export function enrichAlbum(id: string): Promise<AlbumEnrichResponse> {
  return apiRequest<AlbumEnrichResponse>(`/albums/${id}/enrich`, {
    method: "POST",
  });
}

export { providerSyncAlbum } from "./providerSync";
