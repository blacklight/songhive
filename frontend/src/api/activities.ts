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

export type ActivitySourceType = "local" | "remote";

export interface ActivityMentionResponse {
  handle: string;
  actor_url?: string | null;
  user_id?: string | null;
}

/**
 * ActivityPub ``attachment`` entry of an activity's embedded object — a
 * ``Document``/``Image`` for uploaded files and ``Audio`` for hosted tracks.
 */
export interface ActivityAttachment {
  type?: string;
  mediaType?: string;
  url?: string;
  name?: string;
  id?: string;
  duration?: string;
  [key: string]: unknown;
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
  liked: boolean;
  boosted: boolean;
  can_interact: boolean;
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

export interface ActivityReplyRequest {
  status?: string | null;
  content_type?: string;
  visibility?: ActivityVisibility | null;
  language?: string | null;
  media_ids?: string[];
  track_ids?: string[];
}

export interface ActivityUpdate {
  content?: string | null;
  visibility?: ActivityVisibility | null;
  content_type?: string | null;
  language?: string | null;
  media_ids?: string[];
  track_ids?: string[];
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
  },
): Promise<ActivityListResponse> {
  return apiRequest<ActivityListResponse>(`/users/${username}/activities`, {
    query: params,
  });
}

export function getActivity(activityId: string): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>(`/activities/${activityId}`);
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
