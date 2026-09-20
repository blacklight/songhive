import { apiRequest } from "./client";
import { API_PREFIX } from "./config";
import type { QueueTrack } from "@/player/types";

/**
 * Local response types for the podcasts API. ``types.ts`` is regenerated
 * from a running server (``npm run api:gen``); until then these interfaces
 * mirror the backend ``PodcastResponse``/``PodcastEpisodeResponse`` schemas.
 */
export interface PodcastResponse {
  id: string;
  feed_url: string;
  title: string;
  description: string | null;
  author: string | null;
  link: string | null;
  image_url: string | null;
  language: string | null;
  categories: string[];
  explicit: boolean;
  episode_count: number;
  unplayed_count: number;
  latest_episode_at: string | null;
  last_fetched_at: string | null;
  last_error: string | null;
  following: boolean;
}

export interface PodcastEpisodeResponse {
  id: string;
  podcast_id: string;
  guid: string;
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

export interface OpmlImportResponse {
  subscribed: number;
  skipped: number;
  failed: number;
  errors: string[];
}

export type PodcastSortBy = "latest" | "episodes" | "unplayed" | "name";

export function listPodcasts(params?: {
  q?: string;
  limit?: number;
  offset?: number;
  sort_by?: PodcastSortBy | string;
  sort_dir?: "asc" | "desc";
}): Promise<PodcastResponse[]> {
  return apiRequest<PodcastResponse[]>("/podcasts/", { query: params });
}

export function followPodcast(feedUrl: string): Promise<PodcastResponse> {
  return apiRequest<PodcastResponse>("/podcasts/", {
    method: "POST",
    body: { feed_url: feedUrl },
  });
}

export function getPodcast(podcastId: string): Promise<PodcastResponse> {
  return apiRequest<PodcastResponse>(`/podcasts/${podcastId}`);
}

export function unfollowPodcast(podcastId: string): Promise<void> {
  return apiRequest<void>(`/podcasts/${podcastId}`, { method: "DELETE" });
}

export function refreshPodcast(podcastId: string): Promise<PodcastResponse> {
  return apiRequest<PodcastResponse>(`/podcasts/${podcastId}/refresh`, {
    method: "POST",
  });
}

export function listEpisodes(
  podcastId: string,
  params?: { limit?: number; offset?: number; sort?: "newest" | "oldest" },
): Promise<PodcastEpisodeResponse[]> {
  return apiRequest<PodcastEpisodeResponse[]>(
    `/podcasts/${podcastId}/episodes`,
    { query: params },
  );
}

export function markEpisodePlayed(episodeId: string): Promise<void> {
  return apiRequest<void>(`/podcasts/episodes/${episodeId}/played`, {
    method: "POST",
  });
}

export function markEpisodeUnplayed(episodeId: string): Promise<void> {
  return apiRequest<void>(`/podcasts/episodes/${episodeId}/played`, {
    method: "DELETE",
  });
}

/**
 * OPML export is a file download, not a JSON payload — expose the URL so
 * the view can hand it to an anchor and let the browser stream it.
 */
export function opmlExportUrl(): string {
  return `${API_PREFIX}/podcasts/opml`;
}

export function importOpml(file: File): Promise<OpmlImportResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<OpmlImportResponse>("/podcasts/opml/import", {
    method: "POST",
    body,
  });
}

/**
 * Build a ``QueueTrack`` for a podcast episode. Episodes have no local
 * track row — the player streams ``audio_url`` directly via
 * ``stream_url`` and ``remote`` skips library links/history reporting.
 */
export function episodeToQueueTrack(
  episode: PodcastEpisodeResponse,
  podcast: PodcastResponse,
): QueueTrack {
  return {
    id: episode.id,
    title: episode.title,
    artist_id: "",
    artist_name: podcast.author ?? podcast.title,
    album_title: podcast.title,
    artwork_url: episode.image_url ?? podcast.image_url ?? undefined,
    duration: episode.duration_seconds ?? undefined,
    visibility: "public",
    tags: [],
    genres: [],
    is_external: false,
    stream_url: episode.audio_url,
    remote: true,
    remote_url: episode.link ?? undefined,
    podcast_episode_id: episode.id,
    in_collection: false,
  };
}
