import type { RemoteObject } from "@/api/remote";
import type { QueueTrack } from "@/player/types";

/**
 * Turn a cached remote resource into a ``QueueTrack``.
 *
 * Remote objects have no local ``Track`` row — they play through the
 * ``stream_url`` playback endpoint (which resolves the object's own
 * ``audio/*`` link or a cached rendition at play time) and are flagged
 * ``remote`` so library links and listen-history reporting are skipped.
 * Returns ``null`` when the object has nothing playable.
 */
export function remoteObjectToQueueTrack(obj: RemoteObject): QueueTrack | null {
  const media = obj.stream_url ?? obj.audio_url;
  if (!media) return null;
  return {
    id: obj.canonical_url,
    title: obj.name ?? obj.canonical_url,
    artist_id: "",
    artist_name: obj.artist_name ?? "",
    album_title: obj.album_name ?? obj.parent?.name ?? undefined,
    artwork_url: obj.image_url ?? undefined,
    duration: obj.duration ?? undefined,
    visibility: "public",
    tags: [],
    genres: [],
    is_external: false,
    stream_url: media,
    remote: true,
    remote_url: obj.canonical_url,
    in_collection: obj.in_collection ?? false,
  };
}

/**
 * Playable queue for a remote resource: the object itself when it carries
 * audio, else its cached children that do (album/library/playlist items).
 */
export function remoteObjectPlayableTracks(obj: RemoteObject): QueueTrack[] {
  const self = remoteObjectToQueueTrack(obj);
  if (self) return [self];
  return (obj.items ?? [])
    .map((item) => remoteObjectToQueueTrack(item))
    .filter((t): t is QueueTrack => t !== null);
}
