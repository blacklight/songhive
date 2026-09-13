import { apiRequest } from "./client";
import type { ActivityResponse, ActivityVisibility } from "./activities";

// Mirror ``songhive/services/mentions.py`` — the two source formats the
// mention pipeline can render into an activity's HTML ``content``.
export const STATUS_CONTENT_TYPE_PLAIN = "text/plain";
export const STATUS_CONTENT_TYPE_MARKDOWN = "text/markdown";
export type StatusContentType =
  typeof STATUS_CONTENT_TYPE_PLAIN | typeof STATUS_CONTENT_TYPE_MARKDOWN;

export const STATUS_CONTENT_TYPES: StatusContentType[] = [
  STATUS_CONTENT_TYPE_MARKDOWN,
  STATUS_CONTENT_TYPE_PLAIN,
];

export interface StatusCreateRequest {
  status?: string | null;
  content_type?: StatusContentType;
  visibility?: ActivityVisibility;
  language?: string | null;
  media_ids?: string[];
  track_ids?: string[];
}

export function createStatus(
  body: StatusCreateRequest,
): Promise<ActivityResponse> {
  return apiRequest<ActivityResponse>("/statuses/", {
    method: "POST",
    body,
  });
}
