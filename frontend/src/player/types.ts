import type { components } from "@/api/types";

export type TrackResponse = components["schemas"]["TrackResponse"];

export type QueueTrack = TrackResponse & {
  artist_name: string;
  album_title?: string;
  artwork_url?: string;
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
