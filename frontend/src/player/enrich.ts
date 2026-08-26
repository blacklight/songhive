import type { TrackResponse, QueueTrack } from "./types";

export interface TrackEnrich {
  artist_name?: string;
  album_title?: string;
  artwork_url?: string;
}

/**
 * Turn a bare TrackResponse into a QueueTrack the player can consume.
 * `artist_name` is required for QueueTrack, so it falls back to an empty
 * string when the backend has not denormalized it.
 */
export function toQueueTrack(
  track: TrackResponse,
  enrich?: TrackEnrich | null,
): QueueTrack {
  return {
    ...track,
    artist_name: enrich?.artist_name ?? "",
    album_title: enrich?.album_title,
    artwork_url: enrich?.artwork_url,
  };
}

/**
 * Enrich a list of TrackResponse objects using an optional lookup map keyed
 * by track id. Pages that already have artist/album context can pass a lookup
 * to avoid a second round-trip for display names.
 */
export function enrichTracks(
  tracks: TrackResponse[],
  lookup?: Map<string, TrackEnrich>,
): QueueTrack[] {
  return tracks.map((track) => toQueueTrack(track, lookup?.get(track.id)));
}
