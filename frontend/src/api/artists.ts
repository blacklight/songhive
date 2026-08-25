import type { components } from "./types";
import { apiRequest } from "./client";

export type ArtistResponse = components["schemas"]["ArtistResponse"];

export function listArtists(params?: {
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<ArtistResponse[]> {
  return apiRequest<ArtistResponse[]>("/artists/", { query: params });
}

export function getArtist(id: string): Promise<ArtistResponse> {
  return apiRequest<ArtistResponse>(`/artists/${id}`);
}
