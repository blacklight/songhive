import { apiRequest } from "./client";

export interface PlaybackSessionOutput {
  id: string;
  output_kind: "web" | "stream";
  output_stream_id: string | null;
  connection_id: string | null;
  status: string;
  last_error: string | null;
  latency_offset_ms: number | null;
  name: string | null;
}

export interface QueueTrackData {
  id: string;
  title: string;
  artist?: string;
  album?: string;
  duration?: number | null;
  artist_id?: string;
  album_id?: string | null;
  image_url?: string | null;
  visibility?: string;
  remote?: boolean;
  remote_url?: string;
  stream_url?: string;
  podcast_episode_id?: string;
  podcast_id?: string;
}

export interface PlaybackSessionState {
  session_id: string;
  user_id: string;
  state: "idle" | "playing" | "paused";
  current_index: number;
  position_seconds: number;
  position_anchor_at: string | null;
  live_position_seconds: number;
  repeat: "off" | "all" | "one";
  shuffle: boolean;
  controller_connection_id: string | null;
  queue: QueueTrackData[];
  outputs: PlaybackSessionOutput[];
  last_active_at: string | null;
}

export interface OutputSelectionRequest {
  output_ids: string[];
  connection_id?: string | null;
}

export interface CommandRequest {
  command: string;
  args?: Record<string, unknown>;
  connection_id?: string | null;
}

export function getPlaybackSession(): Promise<PlaybackSessionState> {
  return apiRequest<PlaybackSessionState>("/playback/session");
}

export function setSessionOutputs(
  body: OutputSelectionRequest,
): Promise<PlaybackSessionState> {
  return apiRequest<PlaybackSessionState>("/playback/session/outputs", {
    method: "POST",
    body,
  });
}

export function sendPlaybackCommand(
  body: CommandRequest,
): Promise<PlaybackSessionState> {
  return apiRequest<PlaybackSessionState>("/playback/session/command", {
    method: "POST",
    body,
  });
}
