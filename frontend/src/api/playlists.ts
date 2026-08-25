import type { components } from "./types";
import { apiRequest } from "./client";

export type PlaylistResponse = components["schemas"]["PlaylistResponse"];
export type PlaylistCreate = components["schemas"]["PlaylistCreate"];
export type Visibility = components["schemas"]["Visibility"];
export type TrackResponse = components["schemas"]["TrackResponse"];

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

export interface AddTracksToPlaylistRequest {
  track_ids?: string[];
  album_id?: string;
  artist_id?: string;
}

export interface AddTracksToPlaylistResponse {
  added: number;
  track_ids: string[];
}

export function addTracksToPlaylist(
  id: string,
  body: AddTracksToPlaylistRequest,
): Promise<AddTracksToPlaylistResponse> {
  return apiRequest<AddTracksToPlaylistResponse>(`/playlists/${id}/tracks`, {
    method: "POST",
    body,
  });
}

export function listPlaylistTracks(
  id: string,
  params?: { limit?: number; offset?: number },
): Promise<TrackResponse[]> {
  return apiRequest<TrackResponse[]>(`/playlists/${id}/tracks`, {
    query: params,
  });
}

export interface RemoveTracksFromPlaylistRequest {
  track_ids: string[];
}

export interface RemoveTracksFromPlaylistResponse {
  removed: number;
  track_ids: string[];
}

export function removeTracksFromPlaylist(
  id: string,
  body: RemoveTracksFromPlaylistRequest,
): Promise<RemoveTracksFromPlaylistResponse> {
  return apiRequest<RemoveTracksFromPlaylistResponse>(
    `/playlists/${id}/tracks/remove`,
    {
      method: "POST",
      body,
    },
  );
}
