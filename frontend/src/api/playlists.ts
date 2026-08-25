import type { components } from "./types";
import { apiRequest } from "./client";

export type PlaylistResponse = components["schemas"]["PlaylistResponse"];
export type PlaylistCreate = components["schemas"]["PlaylistCreate"];
export type Visibility = components["schemas"]["Visibility"];

export function listPlaylists(params?: {
  limit?: number;
  offset?: number;
}): Promise<PlaylistResponse[]> {
  return apiRequest<PlaylistResponse[]>("/playlists/", { query: params });
}

export function createPlaylist(
  body: PlaylistCreate,
  params?: { visibility?: Visibility },
): Promise<PlaylistResponse> {
  return apiRequest<PlaylistResponse>("/playlists/", {
    method: "POST",
    body,
    query: params,
  });
}

export function getPlaylist(id: string): Promise<PlaylistResponse> {
  return apiRequest<PlaylistResponse>(`/playlists/${id}`);
}
