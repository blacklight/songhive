import type { components } from "@/api/types";

export type TrackResponse = components["schemas"]["TrackResponse"];

export type QueueTrack = TrackResponse & {
  artist_name: string;
  album_title?: string;
  artwork_url?: string;
  /**
   * Direct stream URL for audio not stored in the local library (e.g.
   * remote activity attachments); takes precedence over the
   * ``/api/v1/stream/{id}`` endpoint.
   */
  stream_url?: string;
  /**
   * Attachment-only remote audio without a local track row — library links
   * and listen-history reporting are skipped.
   */
  remote?: boolean;
  /** Canonical page URL on the origin instance, for remote queue tracks. */
  remote_url?: string;
  /**
   * Podcast episode id for remote queue tracks backed by the local podcast
   * catalog — the player marks it played once the listen threshold is met.
   */
  podcast_episode_id?: string;
};

export type RepeatMode = "off" | "all" | "one";
export type PlaybackState = "idle" | "loading" | "playing" | "paused" | "error";

export interface EngineCallbacks {
  onTimeUpdate?: (time: number) => void;
  onDuration?: (duration: number) => void;
  onEnded?: () => void;
  onStateChange?: (state: PlaybackState) => void;
  onError?: (error: MediaError | null) => void;
}

export interface EngineApi {
  load(track: QueueTrack, startAt?: number): void;
  play(): void;
  pause(): void;
  seek(seconds: number): void;
  setVolume(volume: number, muted: boolean): void;
  setNextTrack(track: QueueTrack | null): void;
  destroy(): void;
}
