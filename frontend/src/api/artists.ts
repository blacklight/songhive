import type { components } from "./types";
import { apiRequest } from "./client";

export type ArtistResponse = components["schemas"]["ArtistResponse"];
export type ArtistUpdate = components["schemas"]["ArtistUpdate"];

export function listArtists(params?: {
  q?: string;
  limit?: number;
  offset?: number;
  include?: string;
}): Promise<ArtistResponse[]> {
  return apiRequest<ArtistResponse[]>("/artists/", { query: params });
}

export function getArtist(
  id: string,
  params?: { include?: string },
): Promise<ArtistResponse> {
  if (params) {
    return apiRequest<ArtistResponse>(`/artists/${id}`, { query: params });
  }
  return apiRequest<ArtistResponse>(`/artists/${id}`);
}

export function updateArtist(
  id: string,
  body: ArtistUpdate,
): Promise<ArtistResponse> {
  return apiRequest<ArtistResponse>(`/artists/${id}`, {
    method: "PATCH",
    body,
  });
}

export function deleteArtist(id: string, recursive = false): Promise<void> {
  return apiRequest<void>(`/artists/${id}`, {
    method: "DELETE",
    query: { recursive },
  });
}

export function uploadArtistImage(
  id: string,
  file: File,
): Promise<ArtistResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<ArtistResponse>(`/artists/${id}/image`, {
    method: "POST",
    body,
  });
}

export function deleteArtistImage(id: string): Promise<ArtistResponse> {
  return apiRequest<ArtistResponse>(`/artists/${id}/image`, {
    method: "DELETE",
  });
}

export function uploadArtistCover(
  id: string,
  file: File,
): Promise<ArtistResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<ArtistResponse>(`/artists/${id}/cover`, {
    method: "POST",
    body,
  });
}

export function deleteArtistCover(id: string): Promise<ArtistResponse> {
  return apiRequest<ArtistResponse>(`/artists/${id}/cover`, {
    method: "DELETE",
  });
}
