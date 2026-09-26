import type { components } from "./types";
import { apiRequest, apiRequestWithHeaders } from "./client";
import type { RemoteObject } from "./remote";
import type { QueueTrack } from "@/player/types";
import { remoteObjectToQueueTrack } from "@/utils/remoteObject";

export type PlaylistResponse = components["schemas"]["PlaylistResponse"];
export type PlaylistCreate = components["schemas"]["PlaylistCreate"];
export type PlaylistUpdate = components["schemas"]["PlaylistUpdate"];
export type Visibility = components["schemas"]["Visibility"];
export type TrackResponse = components["schemas"]["TrackResponse"];

/**
 * Podcast episode data embedded in a playlist item. Mirrors the backend
 * ``PlaylistEpisodeItem`` schema (types.ts is regenerated separately).
 */
export interface PlaylistEpisodeItem {
  id: string;
  podcast_id: string;
  podcast_title: string;
  title: string;
  description: string | null;
  link: string | null;
  image_url: string | null;
  audio_url: string;
  audio_type: string | null;
  audio_length: number | null;
  duration_seconds: number | null;
  published_at: string | null;
  season_number: number | null;
  episode_number: number | null;
  episode_type: string | null;
  played: boolean;
}

/**
 * One ordered playlist entry — a local track, a podcast episode, or a
 * cached remote object (federated track).
 */
export interface PlaylistItemResponse {
  item_id: string;
  position: number;
  type: "track" | "episode" | "remote";
  track: TrackResponse | null;
  episode: PlaylistEpisodeItem | null;
  remote?: RemoteObject | null;
}

export function listPlaylists(params?: {
  q?: string;
  owner_username?: string;
  collection?: boolean;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<PlaylistResponse[]> {
  return apiRequest<PlaylistResponse[]>("/playlists/", { query: params });
}

export interface ListPlaylistsResult {
  items: PlaylistResponse[];
  offset: number;
  total: number;
}

export async function listPlaylistsWithMeta(params?: {
  q?: string;
  owner_username?: string;
  collection?: boolean;
  limit?: number;
  offset?: number;
  include?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<ListPlaylistsResult> {
  const response = await apiRequestWithHeaders<PlaylistResponse[]>(
    "/playlists/",
    {
      query: params,
    },
  );
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
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

export interface PlaylistStats {
  track_count: number;
  episode_count?: number;
  total_duration: number;
}

export function getPlaylistStats(id: string): Promise<PlaylistStats> {
  return apiRequest<PlaylistStats>(`/playlists/${id}/stats`);
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
  episode_ids?: string[];
  /** Adds every cataloged episode of the podcast, oldest first. */
  podcast_id?: string;
  /**
   * Cached remote object ids — remote tracks resolve to themselves, remote
   * containers (album/artist/library) expand to their cached tracks.
   */
  remote_object_ids?: string[];
  allow_duplicates?: boolean;
}

export interface AddTracksToPlaylistResponse {
  added: number;
  track_ids: string[];
  episode_ids?: string[];
  remote_object_ids?: string[];
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
    q?: string;
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
  track_ids?: string[];
  episode_ids?: string[];
  remote_object_ids?: string[];
}

export interface RemoveTracksFromPlaylistResponse {
  removed: number;
  track_ids: string[];
  episode_ids?: string[];
  remote_object_ids?: string[];
}

export interface ReorderPlaylistTracksRequest {
  /** Entity ids — track ids or podcast episode ids — moved as a block. */
  item_ids?: string[];
  /** Deprecated alias for ``item_ids``. */
  track_ids?: string[];
  position?: number | null;
}

export interface ReorderPlaylistTracksResponse {
  reordered: boolean;
  item_ids: string[];
  track_ids: string[];
  count: number;
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

export function reorderPlaylistTracks(
  id: string,
  body: ReorderPlaylistTracksRequest,
): Promise<ReorderPlaylistTracksResponse> {
  return apiRequest<ReorderPlaylistTracksResponse>(
    `/playlists/${id}/tracks/reorder`,
    {
      method: "POST",
      body,
    },
  );
}

export function listPlaylistItems(
  id: string,
  params?: {
    q?: string;
    limit?: number;
    offset?: number;
    include?: string;
    sort_by?: string;
    sort_dir?: "asc" | "desc";
  },
): Promise<PlaylistItemResponse[]> {
  return apiRequest<PlaylistItemResponse[]>(`/playlists/${id}/items`, {
    query: params,
  });
}

/**
 * Map a playlist item to a QueueTrack the player can consume: track items
 * carry a full TrackResponse; episode items become remote queue tracks that
 * stream the episode's enclosure URL; remote items become remote queue
 * tracks that play through the remote stream-resolution endpoint.
 */
export function playlistItemToQueueTrack(
  item: PlaylistItemResponse,
): QueueTrack | null {
  if (item.type === "track" && item.track) {
    return item.track as QueueTrack;
  }
  if (item.type === "remote" && item.remote) {
    // Keep unplayable remote items in the list so they can be removed.
    return remoteObjectToQueueTrack(item.remote, { requirePlayable: false });
  }
  if (item.type === "episode" && item.episode) {
    const episode = item.episode;
    return {
      id: episode.id,
      title: episode.title,
      artist_id: "",
      artist_name: episode.podcast_title,
      artwork_url: episode.image_url ?? undefined,
      duration: episode.duration_seconds ?? undefined,
      visibility: "public",
      tags: [],
      genres: [],
      extra_artists: [],
      is_external: false,
      stream_url: episode.audio_url,
      remote: true,
      remote_url: episode.link ?? undefined,
      podcast_episode_id: episode.id,
      podcast_id: episode.podcast_id,
      in_collection: false,
    };
  }
  return null;
}
