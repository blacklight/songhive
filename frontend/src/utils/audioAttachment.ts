import type { ActivityAttachment } from "@/api/activities";
import type { QueueTrack } from "@/player/types";

/**
 * Normalized view of an activity's audio attachment.
 *
 * Songhive stamps structured metadata as namespaced ``songhive:*`` keys
 * (see ``songhive/federation/serializers.py``); attachments from other
 * servers carry only the generic ``name``/``url``/``duration`` fields, so
 * every field degrades gracefully.
 */
export interface AudioAttachmentInfo {
  /** Direct media URL — the ``<audio>`` source and remote stream URL. */
  url: string;
  /** Track title — ``songhive:trackTitle``, else the attachment ``name``. */
  title?: string;
  artist?: string;
  album?: string;
  /** Cover art — the attachment's ActivityStreams ``image`` entry. */
  imageUrl?: string;
  /** Seconds, parsed from the ISO-8601 ``duration`` when present. */
  duration?: number;
  /**
   * Local-library track id — the ``songhive:trackId`` stamped by the
   * *originating* instance, only meaningful when the activity is local.
   */
  trackId?: string;
  /** Canonical track page URL — points at the origin instance for federated posts. */
  trackUrl?: string;
  /** AP object id of the attachment — stable dedupe key for remote audio. */
  objectId?: string;
}

const ISO_DURATION_RE =
  /^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$/;

export function parseIsoDuration(value: unknown): number | undefined {
  if (typeof value !== "string") return undefined;
  const match = ISO_DURATION_RE.exec(value);
  if (!match) return undefined;
  const [, hours, minutes, seconds] = match;
  const total =
    (hours ? Number(hours) * 3600 : 0) +
    (minutes ? Number(minutes) * 60 : 0) +
    (seconds ? Number(seconds) : 0);
  return total > 0 ? total : undefined;
}

function strField(
  attachment: ActivityAttachment,
  key: string,
): string | undefined {
  const value = attachment[key];
  return typeof value === "string" && value ? value : undefined;
}

/**
 * Extract a URL from an ActivityStreams ``image`` value — a bare URL
 * string, an ``Image`` object, or a ``Link``/``Link`` list.
 */
function imageUrl(value: unknown): string | undefined {
  if (typeof value === "string") return value || undefined;
  if (!value || typeof value !== "object") return undefined;
  const url = (value as { url?: unknown }).url;
  if (typeof url === "string") return url || undefined;
  const links = Array.isArray(url) ? url : url ? [url] : [];
  for (const link of links) {
    if (link && typeof link === "object") {
      const href = (link as { href?: unknown }).href;
      if (typeof href === "string" && href) return href;
    }
  }
  return undefined;
}

export function audioAttachmentInfo(
  attachment: ActivityAttachment,
): AudioAttachmentInfo {
  return {
    url: String(attachment.url ?? ""),
    title: strField(attachment, "songhive:trackTitle") ?? attachment.name,
    artist: strField(attachment, "songhive:artistName"),
    album: strField(attachment, "songhive:albumName"),
    imageUrl: imageUrl(attachment.image),
    duration: parseIsoDuration(attachment.duration),
    trackId: strField(attachment, "songhive:trackId"),
    trackUrl: strField(attachment, "songhive:trackUrl"),
    objectId: strField(attachment, "id"),
  };
}

/**
 * Synthesize a ``QueueTrack`` for audio that has no local track row — a
 * remote (or deleted) attachment handed to the player plays through its
 * direct ``stream_url`` and is flagged ``remote`` so library links and
 * listen-history reporting are skipped.
 */
export function attachmentToQueueTrack(
  info: AudioAttachmentInfo,
  fallbackArtwork?: string,
): QueueTrack {
  return {
    id: info.objectId ?? info.url,
    title: info.title ?? info.url,
    artist_id: "",
    artist_name: info.artist ?? "",
    album_title: info.album,
    artwork_url: info.imageUrl ?? fallbackArtwork,
    duration: info.duration,
    visibility: "public",
    tags: [],
    genres: [],
    extra_artists: [],
    is_external: false,
    stream_url: info.url,
    remote: true,
    remote_url: info.trackUrl,
    in_collection: false,
  };
}
