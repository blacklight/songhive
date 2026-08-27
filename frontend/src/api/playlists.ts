import type { components } from "./types";
import { apiRequest } from "./client";

export type PlaylistResponse = components["schemas"]["PlaylistResponse"];
export type PlaylistCreate = components["schemas"]["PlaylistCreate"];
export type PlaylistUpdate = components["schemas"]["PlaylistUpdate"];
export type Visibility = components["schemas"]["Visibility"];
export type TrackResponse = components["schemas"]["TrackResponse"];

export function listPlaylists(params?: {
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
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

export function getPlaylist(
  id: string,
  params?: { include?: string },
): Promise<PlaylistResponse> {
  if (params) {
    return apiRequest<PlaylistResponse>(`/playlists/${id}`, { query: params });
  }
  return apiRequest<PlaylistResponse>(`/playlists/${id}`);
}

export function updatePlaylist(
  id: string,
  body: PlaylistUpdate,
): Promise<PlaylistResponse> {
  return apiRequest<PlaylistResponse>(`/playlists/${id}`, {
    method: "PATCH",
    body,
  });
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
  params?: {
    limit?: number;
    offset?: number;
    include?: string;
    sort_by?: string;
    sort_dir?: "asc" | "desc";
  },
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

export function deletePlaylist(id: string, recursive = false): Promise<void> {
  return apiRequest<void>(`/playlists/${id}`, {
    method: "DELETE",
    query: { recursive },
  });
}

export function uploadPlaylistImage(
  id: string,
  file: File,
): Promise<PlaylistResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<PlaylistResponse>(`/playlists/${id}/image`, {
    method: "POST",
    body,
  });
}

export function deletePlaylistImage(id: string): Promise<PlaylistResponse> {
  return apiRequest<PlaylistResponse>(`/playlists/${id}/image`, {
    method: "DELETE",
  });
}

export function uploadPlaylistCover(
  id: string,
  file: File,
): Promise<PlaylistResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<PlaylistResponse>(`/playlists/${id}/cover`, {
    method: "POST",
    body,
  });
}

export function deletePlaylistCover(id: string): Promise<PlaylistResponse> {
  return apiRequest<PlaylistResponse>(`/playlists/${id}/cover`, {
    method: "DELETE",
  });
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
