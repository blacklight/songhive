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
  include?: string;
}): Promise<AlbumResponse[]> {
  return apiRequest<AlbumResponse[]>("/albums/", { query: params });
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
