import { apiRequest, apiRequestWithHeaders } from "./client";
import type { components } from "./types";
import type { ActivityResponse } from "./activities";
import type { ActivitySubscriptionState } from "./users";

// These interfaces mirror the Pydantic response models in
// ``songhive/api/routes/remote.py``. Regenerate ``api/types.ts`` with
// ``npm run api:gen`` to pick up the generated counterparts.

export type RemoteActor = components["schemas"]["RemoteActorResponse"];

export type RemoteResourceKind =
  "track" | "album" | "artist" | "playlist" | "library";

export interface RemoteObject {
  id: string;
  canonical_url: string;
  object_type: string;
  resource_type?: string | null;
  domain: string;
  actor_url: string;
  actor_handle?: string | null;
  name?: string | null;
  summary?: string | null;
  content?: string | null;
  image_url?: string | null;
  audio_url?: string | null;
  stream_url?: string | null;
  /** Playback hints from the cached document payload (music resources). */
  duration?: number | null;
  artist_name?: string | null;
  album_name?: string | null;
  visibility: string;
  fetched_at?: string | null;
  unavailable: boolean;
  /** Internal SPA route. */
  url: string;
  /** Whether the caller has this object in their collection. */
  in_collection?: boolean;
  /** Whether the caller favorited this object (remote tracks). */
  favorited?: boolean;
  /** The caller's object-follow state (``pending``/``accepted``). */
  follow_state?: string | null;
  /** Materialized activity id — the entry point for like/boost/reply/quote. */
  activity_id?: string | null;
  /** Containing resource (album of a track, library of an upload). */
  parent?: RemoteObject | null;
  /** Cached children — e.g. the tracks of a remote album or library. */
  items?: RemoteObject[];
}

export interface RemoteLookupResponse {
  /** ``local`` = the input was a local URL/handle; ``url`` is its SPA route. */
  kind: "actor" | "object" | "resource" | "local";
  /** Internal SPA route to navigate to. */
  url: string;
  actor?: RemoteActor | null;
  object?: RemoteObject | null;
  activity?: ActivityResponse | null;
}

export interface RemoteObjectDetail {
  object: RemoteObject;
  activity?: ActivityResponse | null;
}

export interface RemoteObjectList {
  items: RemoteObject[];
}

export interface RemoteActorActivities {
  activities: ActivityResponse[];
  total: number;
}

/**
 * Explicit remote lookup — dereferences a handle or URL through the
 * guarded fetch path. Use for search-box lookups; renders navigate to
 * the returned internal ``url``.
 */
export function remoteLookup(
  input: string,
  options?: { refresh?: boolean },
): Promise<RemoteLookupResponse> {
  return apiRequest<RemoteLookupResponse>("/remote/lookup", {
    query: { input, refresh: options?.refresh ?? undefined },
  });
}

/** Resolve a ``user@domain`` handle to a remote actor (fetch-on-miss). */
export function getRemoteActor(
  handle: string,
  options?: { refresh?: boolean },
): Promise<RemoteActor> {
  return apiRequest<RemoteActor>(
    `/remote/actors/${encodeURIComponent(handle)}`,
    { query: { refresh: options?.refresh ?? undefined } },
  );
}

/** List already-cached activities for a remote actor — no remote fetch. */
export function getRemoteActorActivities(
  handle: string,
  options?: { limit?: number; offset?: number; reveal?: boolean },
): Promise<RemoteActorActivities> {
  return apiRequest<RemoteActorActivities>(
    `/remote/actors/${encodeURIComponent(handle)}/activities`,
    {
      query: {
        limit: options?.limit,
        offset: options?.offset,
        reveal: options?.reveal || undefined,
      },
    },
  );
}

/**
 * Subscribe to a remote actor's activity notifications (the profile
 * bell). Also follows the actor on a best-effort basis — remote
 * activities only arrive while a local user follows them — so the
 * returned ``follow_state`` reflects the resulting follow.
 */
export function subscribeToRemoteActorActivity(
  handle: string,
): Promise<ActivitySubscriptionState> {
  return apiRequest<ActivitySubscriptionState>(
    `/remote/actors/${encodeURIComponent(handle)}/activity-subscription`,
    { method: "POST" },
  );
}

/** Remove the activity subscription on a remote actor (keeps the follow). */
export function unsubscribeFromRemoteActorActivity(
  handle: string,
): Promise<void> {
  return apiRequest<void>(
    `/remote/actors/${encodeURIComponent(handle)}/activity-subscription`,
    { method: "DELETE" },
  );
}

/**
 * Return a cached remote object and its materialized activity. Powers the
 * ``/activities/@user@domain/{id}`` permalink; ``refresh`` re-fetches the
 * canonical URL to confirm the object still exists remotely.
 */
export function getRemoteObject(
  objectId: string,
  options?: { refresh?: boolean },
): Promise<RemoteObjectDetail> {
  return apiRequest<RemoteObjectDetail>(
    `/remote/objects/${encodeURIComponent(objectId)}`,
    { query: { refresh: options?.refresh ?? undefined } },
  );
}

/** Return a cached remote resource (track/album/artist/playlist/library). */
export function getRemoteResource(
  kind: RemoteResourceKind,
  objectId: string,
): Promise<RemoteObject> {
  return apiRequest<RemoteObject>(
    `/remote/${encodeURIComponent(kind)}/${encodeURIComponent(objectId)}`,
  );
}

/**
 * Browse cached remote resources — no remote fetch. ``collection``
 * restricts to the caller's remote collection closure (collected rows and
 * favorites plus cached parents/children); ``favorites`` to remote
 * favorites; ``library`` to remote members of a local library. ``q``
 * matches name/summary/canonical URL case-insensitively, mirroring the
 * local list search boxes.
 */
export interface RemoteObjectListOptions {
  resource_type?: RemoteResourceKind;
  q?: string;
  collection?: boolean;
  favorites?: boolean;
  library?: string;
  limit?: number;
  offset?: number;
  /** Local browse-list sort fields — the server maps them onto the
   * closest ``remote_objects`` column so remote pages arrive in the
   * merged view's order. */
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export function listRemoteObjects(
  options?: RemoteObjectListOptions,
): Promise<RemoteObjectList> {
  return apiRequest<RemoteObjectList>("/remote/objects", {
    query: {
      resource_type: options?.resource_type,
      q: options?.q,
      collection: options?.collection || undefined,
      favorites: options?.favorites || undefined,
      library: options?.library,
      limit: options?.limit,
      offset: options?.offset,
      sort_by: options?.sort_by,
      sort_dir: options?.sort_dir,
    },
  });
}

export interface RemoteObjectListMeta {
  items: RemoteObject[];
  offset: number;
  total: number;
}

/**
 * Same listing as ``listRemoteObjects`` but surfaces the ``X-Total-Count``
 * header so callers can drive remote pagination alongside the local
 * list's Load More.
 */
export async function listRemoteObjectsWithMeta(
  options?: RemoteObjectListOptions,
): Promise<RemoteObjectListMeta> {
  const response = await apiRequestWithHeaders<RemoteObjectList>(
    "/remote/objects",
    {
      query: {
        resource_type: options?.resource_type,
        q: options?.q,
        collection: options?.collection || undefined,
        favorites: options?.favorites || undefined,
        library: options?.library,
        limit: options?.limit,
        offset: options?.offset,
        sort_by: options?.sort_by,
        sort_dir: options?.sort_dir,
      },
    },
  );
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body.items,
    offset: options?.offset ?? 0,
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.items.length,
  };
}

export interface RemoteObjectFollowState {
  follow_state?: string | null;
}

/**
 * Follow a remote resource — delivers a signed object-scoped ``Follow``
 * to its controlling actor. Following a remote library subscribes the
 * instance to new items published into it.
 */
export function followRemoteObject(
  objectId: string,
): Promise<RemoteObjectFollowState> {
  return apiRequest<RemoteObjectFollowState>(
    `/remote/objects/${encodeURIComponent(objectId)}/follow`,
    { method: "POST" },
  );
}

/** Unfollow a remote resource — delivers ``Undo(Follow)`` remotely. */
export function unfollowRemoteObject(objectId: string): Promise<void> {
  return apiRequest<void>(
    `/remote/objects/${encodeURIComponent(objectId)}/follow`,
    { method: "DELETE" },
  );
}

/** Favorite a cached remote track. Idempotent. */
export function favoriteRemoteObject(objectId: string): Promise<void> {
  return apiRequest<void>(
    `/remote/objects/${encodeURIComponent(objectId)}/favorite`,
    { method: "POST" },
  );
}

/** Remove a remote object favorite. Idempotent. */
export function unfavoriteRemoteObject(objectId: string): Promise<void> {
  return apiRequest<void>(
    `/remote/objects/${encodeURIComponent(objectId)}/favorite`,
    { method: "DELETE" },
  );
}

/**
 * Materialize the ``Activity`` mirror for a cached remote object so
 * like/boost/reply/quote work through the standard activities API.
 * Idempotent — returns the object with its activity.
 */
export function ensureRemoteObjectActivity(
  objectId: string,
): Promise<RemoteObjectDetail> {
  return apiRequest<RemoteObjectDetail>(
    `/remote/objects/${encodeURIComponent(objectId)}/activity`,
    { method: "POST" },
  );
}

/**
 * Record a completed listen of a remote track — the remote counterpart of
 * ``addHistory``. Also enqueues the ``track.scrobble`` submission when the
 * caller has an active scrobble config.
 */
export function addRemoteListen(objectId: string): Promise<void> {
  return apiRequest<void>(
    `/remote/objects/${encodeURIComponent(objectId)}/listen`,
    { method: "POST" },
  );
}

/**
 * Report that playback of a remote track started — the remote counterpart
 * of ``reportNowPlaying``. No-op server-side without a scrobble config.
 */
export function reportRemoteNowPlaying(objectId: string): Promise<void> {
  return apiRequest<void>(
    `/remote/objects/${encodeURIComponent(objectId)}/now-playing`,
    { method: "POST" },
  );
}
