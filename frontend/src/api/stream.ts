import { buildUrl } from "./config";

// Stream URLs are tokenless: the browser authenticates same-origin media
// requests through the HttpOnly access_token cookie. Share links keep using
// their own ?token=<share_token> parameter, set by the caller.
export function streamUrl(
  track: { id: string },
  opts?: { format?: "mp3" | "ogg" | "flac" | "aac" | "opus"; bitrate?: number },
): string {
  const query: Record<string, string | number | undefined | null> = {};
  if (opts?.format) query.format = opts.format;
  if (opts?.bitrate !== undefined) query.bitrate = opts.bitrate;
  return buildUrl(`/api/v1/stream/${track.id}`, query);
}
