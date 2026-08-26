import type { components } from "./types";
import { apiRequest } from "./client";

export type ArtistResponse = components["schemas"]["ArtistResponse"];

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

export function deleteArtist(id: string, recursive = false): Promise<void> {
  return apiRequest<void>(`/artists/${id}`, {
    method: "DELETE",
    query: { recursive },
  });
}
