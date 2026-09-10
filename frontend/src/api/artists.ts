import type { components } from "./types";
import { apiRequest, apiRequestWithHeaders } from "./client";

export type ArtistResponse = components["schemas"]["ArtistResponse"];
export type ArtistUpdate = components["schemas"]["ArtistUpdate"];

export function listArtists(params?: {
  q?: string;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<ArtistResponse[]> {
  return apiRequest<ArtistResponse[]>("/artists/", { query: params });
}

export interface ListArtistsResult {
  items: ArtistResponse[];
  offset: number;
  total: number;
}

export async function listArtistsWithMeta(params?: {
  q?: string;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<ListArtistsResult> {
  const response = await apiRequestWithHeaders<ArtistResponse[]>("/artists/", {
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
