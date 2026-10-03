import { buildUrl } from "./config";

// Stream URLs are tokenless: the browser authenticates same-origin media
// requests through the HttpOnly access_token cookie. Share links keep using
// their own ?token=<share_token> parameter, set by the caller.
// ``stream_url`` carries remote audio (e.g. federated activity
// attachments) that has no local track to stream through the endpoint.
export function streamUrl(
  track: { id: string; stream_url?: string | null },
  opts?: {
    format?: "mp3" | "ogg" | "flac" | "aac" | "opus";
    bitrate?: number;
    // Multi-rendition providers (e.g. YouTube) serve video+audio on demand.
    variant?: "audio" | "video";
  },
): string {
  if (track.stream_url) return track.stream_url;
  const query: Record<string, string | number | undefined | null> = {};
  if (opts?.format) query.format = opts.format;
  if (opts?.bitrate !== undefined) query.bitrate = opts.bitrate;
  if (opts?.variant) query.variant = opts.variant;
  return buildUrl(`/api/v1/stream/${track.id}`, query);
}
