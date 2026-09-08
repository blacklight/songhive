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

export interface ActivityResponse {
  id: string;
  entity_type: string;
  entity_id: string;
  activity_type: ActivityType;
  source_type: ActivitySourceType;
  source_actor: string;
  source_id: string;
  local_object_id?: string | null;
  owner_user_id?: string | null;
  source_actor_avatar_url?: string | null;
  source_actor_display_name?: string | null;
  visibility: ActivityVisibility;
  in_reply_to_activity_id?: string | null;
  content?: string | null;
  content_source?: string | null;
  content_type?: string | null;
  published_at: string;
  mentions: ActivityMentionResponse[];
}

export interface ActivityListResponse {
  activities: ActivityResponse[];
  next_cursor?: string | null;
}

export interface ActivityUpdate {
  content?: string | null;
  visibility?: ActivityVisibility | null;
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

export function updateActivity(
  activityId: string,
  body: ActivityUpdate,
): Promise<{ status: string }> {
  return apiRequest<{ status: string }>(`/activities/${activityId}`, {
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
