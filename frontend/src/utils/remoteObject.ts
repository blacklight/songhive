import type { RemoteObject } from "@/api/remote";
import type { QueueTrack } from "@/player/types";

/**
 * Turn a cached remote resource into a ``QueueTrack``.
 *
 * Remote objects have no local ``Track`` row — they play through the
 * ``stream_url`` playback endpoint (which resolves the object's own
 * ``audio/*`` link or a cached rendition at play time) and are flagged
 * ``remote`` so library links and listen-history reporting are skipped.
 * The queue id is the ``remote_objects`` row id — playlist reorder/removal
 * identify remote rows by it. Returns ``null`` when the object has nothing
 * playable unless ``requirePlayable`` is disabled.
 */
export function remoteObjectToQueueTrack(
  obj: RemoteObject,
  options?: { requirePlayable?: boolean },
): QueueTrack | null {
  const media = obj.stream_url ?? obj.audio_url;
  if (!media && options?.requirePlayable !== false) return null;
  return {
    id: obj.id,
    title: obj.name ?? obj.canonical_url,
    artist_id: "",
    artist_name: obj.artist_name ?? "",
    album_title: obj.album_name ?? obj.parent?.name ?? undefined,
    artwork_url: obj.image_url ?? undefined,
    duration: obj.duration ?? undefined,
    visibility: "public",
    tags: [],
    genres: [],
    extra_artists: [],
    is_external: false,
    stream_url: media ?? undefined,
    remote: true,
    remote_url: obj.canonical_url,
    remote_object_id: obj.id,
    remote_domain: obj.domain,
    remote_page_url: obj.url,
    in_collection: obj.in_collection ?? false,
    favorited: obj.favorited ?? false,
  };
}

/**
 * Playable queue for a remote resource: the object itself when it carries
 * audio, else its cached descendants that do — containers nest one level
 * of children (an artist's albums carry their tracks).
 */
export function remoteObjectPlayableTracks(obj: RemoteObject): QueueTrack[] {
  const self = remoteObjectToQueueTrack(obj);
  if (self) return [self];
  return (obj.items ?? []).flatMap((item) => remoteObjectPlayableTracks(item));
}
