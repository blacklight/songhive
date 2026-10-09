import { apiRequest } from "./client";

export interface StreamOwner {
  username: string;
  display_name?: string | null;
  avatar_url?: string | null;
}

export interface StreamNowPlaying {
  track_id?: string | null;
  title?: string | null;
  artist?: string | null;
  album?: string | null;
}

export type StreamPlaybackState = "idle" | "playing" | "paused";

export interface StreamResponse {
  id: string;
  name: string;
  mount: string;
  /**
   * Mountpoint URL. For token-protected mounts the owner (or an admin)
   * receives the URL with ``?token=`` embedded so the embedded player can
   * connect.
   */
  stream_url: string;
  /** Derived from ``listen_token``: token-protected mounts are private. */
  visibility: "public" | "private";
  is_owner: boolean;
  /** ``true`` when the requester may manage the stream (owner or admin). */
  can_manage: boolean;
  owner: StreamOwner;
  enabled: boolean;
  /** ``true`` while the stream driver is publishing (meta key is live). */
  online: boolean;
  /**
   * State of the playback session driving this mount, when one is attached.
   * An online mount whose session is not ``playing`` is broadcasting
   * silence — the directory reports it as paused, not live.
   */
  playback_state?: StreamPlaybackState | null;
  description?: string | null;
  genre?: string | null;
  format?: string | null;
  bitrate?: string | null;
  content_type?: string | null;
  now_playing?: StreamNowPlaying | null;
  /** Approximate active HTTP listeners on the mount. */
  listener_count: number;
  /** ``true`` while a live device broadcast (not the queue) is on air. */
  live?: boolean;
  /**
   * ``true`` when the requester may open a live broadcast on this mount
   * (owner, enabled output, ``streams.live_enabled``). UI hint only — the
   * ingest WebSocket re-checks everything.
   */
  can_broadcast?: boolean;
  created_at: string;
}

/**
 * Payload of the ``stream_update`` WebSocket event the stream worker
 * publishes when a mount's online state or now-playing metadata changes.
 */
export interface StreamUpdateEvent {
  mount: string;
  online: boolean;
  now_playing?: StreamNowPlaying | null;
  live?: boolean;
}

/**
 * Terminal close codes used by the live ingest socket
 * (``/ws/live/{output_id}``); 1000 is a clean end.
 */
export const LIVE_WS_CLOSE = {
  clean: 1000,
  unauthenticated: 4001,
  forbidden: 4003,
  notFound: 4004,
  conflict: 4409,
  startTimeout: 4415,
  tooSlow: 4429,
  workerUnavailable: 4503,
  serverError: 4504,
} as const;

/** Candidate MediaRecorder mime types, tried in this order. */
export const LIVE_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/ogg;codecs=opus",
  "audio/mp4",
] as const;

export interface StreamUpdateResponse {
  id: string;
  enabled: boolean;
}

export function listStreams(params?: {
  owner_username?: string;
}): Promise<StreamResponse[]> {
  return apiRequest<StreamResponse[]>("/streams/", { query: params });
}

export function updateStream(
  id: string,
  body: { enabled: boolean },
): Promise<StreamUpdateResponse> {
  return apiRequest<StreamUpdateResponse>(`/streams/${id}`, {
    method: "PATCH",
    body,
  });
}

export function sendStreamCommand(
  id: string,
  command: "play" | "pause",
): Promise<{ state: StreamPlaybackState }> {
  return apiRequest<{ state: StreamPlaybackState }>(`/streams/${id}/command`, {
    method: "POST",
    body: { command },
  });
}
