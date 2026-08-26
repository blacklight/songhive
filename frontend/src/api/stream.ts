import { buildUrl } from "./config";

let tokenProvider: (() => string | null) | null = null;

export function setStreamTokenProvider(provider: () => string | null) {
  tokenProvider = provider;
}

// TODO(phase-3): the playback engine will use this helper. The auth mechanism
// (access token in the query string) is intentionally isolated here so it can
// be swapped for a short-lived stream token later without touching the player.
export function streamUrl(
  track: { id: string },
  opts?: { format?: "mp3" | "ogg" | "flac" | "aac" | "opus"; bitrate?: number },
): string {
  const token = tokenProvider ? tokenProvider() : "";
  const query: Record<string, string | number | undefined | null> = {};
  if (token) query.token = token;
  if (opts?.format) query.format = opts.format;
  if (opts?.bitrate !== undefined) query.bitrate = opts.bitrate;
  return buildUrl(`/api/v1/stream/${track.id}`, query);
}
