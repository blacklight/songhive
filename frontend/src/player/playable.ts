import type { QueueTrack } from "./types";

/**
 * Playability predicates for queue tracks. A row may be listed without a
 * playable stream — the UI then links out instead of queuing it.
 */

/**
 * Remote rows cached without playable media can't be queued — clicking
 * their title opens the remote resource page instead.
 */
export function isRemoteUnplayable(track: QueueTrack): boolean {
  return !!track.remote && !track.stream_url && !track.audio_url;
}

/**
 * Provider-backed tracks whose media isn't playable for this viewer (e.g.
 * stream policy denies the proxy URL) still offer the public provider page
 * — ``external_url`` — as the click target instead of a play action.
 */
export function isExternalUnplayable(track: QueueTrack): boolean {
  return !track.audio_url && !track.stream_url && !!track.external_url;
}

/**
 * Sale-gated local tracks with a ``none`` unpaid policy advertise no media
 * URL — the row links to the track page, where the purchase panel lives.
 */
export function isPaidLocked(track: QueueTrack): boolean {
  return (
    !track.remote &&
    !!track.paid &&
    !track.audio_url &&
    !track.stream_url &&
    !track.external_url
  );
}

export function isUnplayable(track: QueueTrack): boolean {
  return (
    isRemoteUnplayable(track) ||
    isExternalUnplayable(track) ||
    isPaidLocked(track)
  );
}
