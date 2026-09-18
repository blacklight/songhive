import { apiRequest } from "./client";
import type { ActivityResponse } from "./activities";

// These interfaces mirror the Pydantic response models in
// ``songhive/api/routes/remote.py``. Regenerate ``api/types.ts`` with
// ``npm run api:gen`` to pick up the generated counterparts.

export interface RemoteActor {
  handle: string;
  username: string;
  domain: string;
  actor_url: string;
  display_name?: string | null;
  summary?: string | null;
  avatar_url?: string | null;
  header_url?: string | null;
  profile_url?: string | null;
  fetched_at?: string | null;
  unavailable: boolean;
  /** Internal SPA route (``/@user@domain``). */
  url: string;
  /** Viewer-relative follow state (``pending``/``accepted``), when authenticated. */
  follow_state?: string | null;
}

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
  visibility: string;
  fetched_at?: string | null;
  unavailable: boolean;
  /** Internal SPA route. */
  url: string;
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
  options?: { limit?: number; offset?: number },
): Promise<RemoteActorActivities> {
  return apiRequest<RemoteActorActivities>(
    `/remote/actors/${encodeURIComponent(handle)}/activities`,
    { query: { limit: options?.limit, offset: options?.offset } },
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
