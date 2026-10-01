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

export interface StreamResponse {
  id: string;
  name: string;
  mount: string;
  /**
   * Mountpoint URL. For token-protected mounts the owner receives the URL
   * with ``?token=`` embedded so the embedded player can connect.
   */
  stream_url: string;
  /** Derived from ``listen_token``: token-protected mounts are private. */
  visibility: "public" | "private";
  is_owner: boolean;
  owner: StreamOwner;
  enabled: boolean;
  /** ``true`` while the stream driver is publishing (meta key is live). */
  online: boolean;
  description?: string | null;
  genre?: string | null;
  format?: string | null;
  bitrate?: string | null;
  content_type?: string | null;
  now_playing?: StreamNowPlaying | null;
  /** Approximate active HTTP listeners on the mount. */
  listener_count: number;
  created_at: string;
}

export function listStreams(): Promise<StreamResponse[]> {
  return apiRequest<StreamResponse[]>("/streams/");
}
