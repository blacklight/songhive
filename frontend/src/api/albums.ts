import type { components } from "./types";
import { apiRequest } from "./client";

export type AlbumResponse = components["schemas"]["AlbumResponse"];
export type AlbumUpdate = components["schemas"]["AlbumUpdate"];

export function listAlbums(params?: {
  q?: string;
  artist_id?: string;
  year_from?: number;
  year_to?: number;
  limit?: number;
  offset?: number;
}): Promise<AlbumResponse[]> {
  return apiRequest<AlbumResponse[]>("/albums/", { query: params });
}

export function getAlbum(id: string): Promise<AlbumResponse> {
  return apiRequest<AlbumResponse>(`/albums/${id}`);
}

export function updateAlbum(
  id: string,
  body: AlbumUpdate,
): Promise<AlbumResponse> {
  return apiRequest<AlbumResponse>(`/albums/${id}`, { method: "PATCH", body });
}

export function deleteAlbum(id: string, recursive = true): Promise<void> {
  return apiRequest<void>(`/albums/${id}`, {
    method: "DELETE",
    query: { recursive },
  });
}
