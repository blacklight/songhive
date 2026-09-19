import { apiRequest } from "./client";

// These interfaces mirror the Pydantic response models in
// ``songhive/api/routes/activities.py``. Regenerate ``api/types.ts`` with
// ``npm run api:gen`` to pick up the generated counterparts.
export type ActivityVisibility =
  "public" | "followers" | "mentioned" | "local" | "private";

export type ActivityType =
  | "create"
  | "announce"
  | "like"
  | "reply"
  | "quote"
  | "mention"
  | "update"
  | "delete"
  | "webmention";

export type ActivitySourceType = "local" | "remote" | "webmention";

export interface ActivityMentionResponse {
  handle: string;
  actor_url?: string | null;
  user_id?: string | null;
}

/**
 * Link-preview card for the first bare URL in an activity — mirrors
 * ``PreviewCardResponse`` in ``songhive/api/routes/activities.py``.
 */
export interface PreviewCardResponse {
  url: string;
  title?: string | null;
  description?: string | null;
  image_url?: string | null;
  site_name?: string | null;
  type?: string;
}

/**
 * ActivityPub ``attachment`` entry of an activity's embedded object — a
 * ``Document``/``Image`` for uploaded files and ``Audio`` for hosted tracks.
 * Track attachments may additionally carry ``songhive:*`` namespaced keys
 * (``songhive:trackId``, ``songhive:trackTitle``, ``songhive:artistName``,
 * ``songhive:albumName``, ``songhive:trackUrl``) and an ``image`` entry
 * with the cover art — see ``audioAttachmentInfo`` for normalization.
 */
export interface ActivityAttachment {
  type?: string;
  mediaType?: string;
  url?: string;
  name?: string;
  id?: string;
  duration?: string;
  image?: unknown;
  [key: string]: unknown;
}

/**
 * Parsed metadata of an incoming Webmention — mirrors
 * ``WebmentionResponse`` in ``songhive/api/routes/activities.py``.
 * ``tags`` carries the source document's mf2 categories (rendered as
 * Songhive tag links); ``mention_type`` is the mf2 interaction kind
 * (``mention``, ``reply``, ``like``, ``repost``, …).
 */
export interface WebmentionResponse {
  source: string;
  target: string;
  title?: string | null;
  excerpt?: string | null;
  author_name?: string | null;
  author_url?: string | null;
  author_photo?: string | null;
  published?: string | null;
  mention_type?: string;
  tags: string[];
}

export interface ActivityResponse {
  id: string;
  entity_type: string;
  entity_id: string;
  activity_type: ActivityType;
  source_type: ActivitySourceType;
  source_actor: string;
  source_id: string;
  local_object_id?: string | null;
  /**
   * Dereferenceable id of the activity's object — the activity's own
   * object id for ``Create``-style payloads, the reacted object's id for
   * ``like``/``announce`` cards.
   */
  object_url?: string | null;
  /** ActivityStreams type of the embedded object (``Note``, ``Audio``, …). */
  object_type?: string | null;
  owner_user_id?: string | null;
  source_actor_avatar_url?: string | null;
  source_actor_display_name?: string | null;
  visibility: ActivityVisibility;
  in_reply_to_activity_id?: string | null;
  content?: string | null;
  content_source?: string | null;
  content_type?: string | null;
  language?: string | null;
  attachments?: ActivityAttachment[];
  published_at: string;
  mentions: ActivityMentionResponse[];
  like_count: number;
  boost_count: number;
  reply_count: number;
  quote_count: number;
  liked: boolean;
  boosted: boolean;
  can_interact: boolean;
  preview_card?: PreviewCardResponse | null;
  webmention?: WebmentionResponse | null;
}

export interface ActivityListResponse {
  activities: ActivityResponse[];
  next_cursor?: string | null;
}

/** A known account that liked or boosted an activity. */
export interface ActivityActorResponse {
  actor: string;
  handle: string;
  display_name?: string | null;
  avatar_url?: string | null;
  username?: string | null;
  profile_url?: string | null;
  published_at?: string | null;
}

export interface ActivityActorListResponse {
  actors: ActivityActorResponse[];
}

/** A federated reply to an activity, serialized from the pubby raw object. */
export interface RemoteReply {
  id: string;
  object_id?: string | null;
  /**
   * Object id of the replied-to node — a local activity's ``source_id`` or
   * another remote reply's ``object_id`` — used to regroup the flat reply
   * list into threads.
   */
  in_reply_to?: string | null;
  source_actor: string;
  source_actor_name?: string | null;
  source_actor_url?: string | null;
  source_actor_avatar_url?: string | null;
  content?: string | null;
  content_type?: string | null;
  language?: string | null;
  attachments: ActivityAttachment[];
  url?: string | null;
  published_at?: string | null;
}

export interface ReplyActivityListResponse {
  activities: ActivityResponse[];
  remote_replies: RemoteReply[];
}

/** A federated quote of an activity, serialized from the pubby raw object. */
export type RemoteQuote = Omit<RemoteReply, "in_reply_to"> & {
  /** Object id of the quoted activity. */
  quoted?: string | null;
};

export interface QuoteActivityListResponse {
  /**
   * Local quote cards — locally authored quotes and remote quotes
   * materialized into ``Activity`` rows.
   */
  activities: ActivityResponse[];
  remote_quotes: RemoteQuote[];
}

/**
 * How a post's audio file attachments are imported into the author's
 * library — mirrors ``AudioImportOptions`` in
 * ``songhive/api/routes/_common.py``.
 */
export interface AudioImportOptions {
  /** Import each attached audio file as a library track (default true). */
  upload_to_library?: boolean;
  /** Enqueue MusicBrainz enrichment for the new tracks (default false). */
  fetch_metadata?: boolean;
  /** Target library id; ``null``/omitted resolves to "Uploads". */
  library_id?: string | null;
}

export interface ActivityReplyRequest {
  status?: string | null;
  content_type?: string;
  visibility?: ActivityVisibility | null;
  language?: string | null;
  media_ids?: string[];
  track_ids?: string[];
  audio_import?: AudioImportOptions | null;
}

export interface ActivityUpdate {
  content?: string | null;
  visibility?: ActivityVisibility | null;
  content_type?: string | null;
  language?: string | null;
  media_ids?: string[];
  track_ids?: string[];
  audio_import?: AudioImportOptions | null;
}

export type ListActivitiesParams = {
  activity_type?: ActivityType | string;
  source_type?: ActivitySourceType;
  cursor?: string;
  limit?: number;
};

export function listEntityActivities(
  entityType: string,
  entityId: string,
  params?: ListActivitiesParams,
): Promise<ActivityListResponse> {
  return apiRequest<ActivityListResponse>(
    `/${entityType}/${entityId}/activities`,
    { query: params },
  );
}

export function listUserActivities(
  username: string,
  params?: ListActivitiesParams & {
    mode?: "posts" | "all";
    include_boosts?: boolean;
    include_replies?: boolean;
    reveal?: boolean;
  },
): Promise<ActivityListResponse> {
  return apiRequest<ActivityListResponse>(`/users/${username}/activities`, {
    query: params,
  });
}

export function getActivity(activityId: string): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>(`/activities/${activityId}`);
}

/**
 * Resolve an object URL/id — a local ``{actor}/objects/{id}`` permalink or
 * a remote object materialized as a reply — to the stored activity row.
 * 404s for objects the instance does not store.
 */
export function lookupActivity(url: string): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>(`/activities/lookup`, {
    query: { url },
  });
}

export function updateActivity(
  activityId: string,
  body: ActivityUpdate,
): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>(`/activities/${activityId}`, {
    method: "PATCH",
    body,
  });
}

export function deleteActivity(
  activityId: string,
): Promise<{ status: string }> {
  return apiRequest<{ status: string }>(`/activities/${activityId}`, {
    method: "DELETE",
  });
}

export function likeActivity(
  activityId: string,
): Promise<{ status: string; activity_id: string }> {
  return apiRequest<{ status: string; activity_id: string }>(
    `/activities/${activityId}/like`,
    { method: "POST" },
  );
}

export function boostActivity(
  activityId: string,
): Promise<{ status: string; activity_id: string }> {
  return apiRequest<{ status: string; activity_id: string }>(
    `/activities/${activityId}/boost`,
    { method: "POST" },
  );
}

export function unlikeActivity(
  activityId: string,
): Promise<{ status: string }> {
  return apiRequest<{ status: string }>(`/activities/${activityId}/like`, {
    method: "DELETE",
  });
}

export function unboostActivity(
  activityId: string,
): Promise<{ status: string }> {
  return apiRequest<{ status: string }>(`/activities/${activityId}/boost`, {
    method: "DELETE",
  });
}

export function replyToActivity(
  activityId: string,
  body: ActivityReplyRequest,
): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>(`/activities/${activityId}/reply`, {
    method: "POST",
    body,
  });
}

export function quoteActivity(
  activityId: string,
  body: ActivityReplyRequest,
): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>(`/activities/${activityId}/quote`, {
    method: "POST",
    body,
  });
}

export function listActivityLikes(
  activityId: string,
): Promise<ActivityActorListResponse> {
  return apiRequest<ActivityActorListResponse>(
    `/activities/${activityId}/likes`,
  );
}

export function listActivityBoosts(
  activityId: string,
): Promise<ActivityActorListResponse> {
  return apiRequest<ActivityActorListResponse>(
    `/activities/${activityId}/boosts`,
  );
}

export function listActivityReplies(
  activityId: string,
): Promise<ReplyActivityListResponse> {
  return apiRequest<ReplyActivityListResponse>(
    `/activities/${activityId}/replies`,
  );
}

export function listActivityQuotes(
  activityId: string,
): Promise<QuoteActivityListResponse> {
  return apiRequest<QuoteActivityListResponse>(
    `/activities/${activityId}/quotes`,
  );
}
