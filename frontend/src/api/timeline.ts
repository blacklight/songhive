import { apiRequest } from "./client";
import type { ActivityListResponse, ActivitySourceType } from "./activities";

/**
 * Cross-entity timeline endpoint (``GET /api/v1/timeline``).
 *
 * ``scope=mine`` returns the caller's own activity and requires
 * authentication; ``scope=instance`` returns the feed of local activities
 * on entities the requester may access — anonymous callers get the public
 * subset; ``scope=federated`` returns the same feed including activities
 * received from remote instances and webmentions. A ``following`` scope is
 * intentionally absent: Songhive does not publish outgoing follows, so
 * there is no remote subscription graph to feed it.
 */
export type TimelineScope = "mine" | "instance" | "federated";
export type TimelineMode = "posts" | "all";

export interface ListTimelineParams {
  [key: string]: string | number | boolean | undefined;
  scope?: TimelineScope;
  mode?: TimelineMode;
  include_boosts?: boolean;
  include_replies?: boolean;
  source_type?: ActivitySourceType;
  cursor?: string;
  limit?: number;
}

export function listTimeline(
  params?: ListTimelineParams,
): Promise<ActivityListResponse> {
  return apiRequest<ActivityListResponse>("/timeline", { query: params });
}
