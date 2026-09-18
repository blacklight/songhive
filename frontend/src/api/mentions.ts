import type { components } from "./types";
import { apiRequestWithHeaders } from "./client";

export type MentionResponse = components["schemas"]["MentionResponse"];

export type MentionSource = "local" | "activitypub" | "webmention";

export const MENTION_SOURCES: MentionSource[] = [
  "local",
  "activitypub",
  "webmention",
];

export type MentionVisibilityFilter = "all" | "private";

export interface MentionListPage {
  items: MentionResponse[];
  total: number;
}

export async function listMentions(params?: {
  limit?: number;
  offset?: number;
  source?: string;
  visibility?: MentionVisibilityFilter;
}): Promise<MentionListPage> {
  const response = await apiRequestWithHeaders<MentionResponse[]>(
    "/mentions/",
    {
      query: {
        limit: params?.limit,
        offset: params?.offset,
        source: params?.source,
        visibility: params?.visibility === "private" ? "private" : undefined,
      },
    },
  );
  const header = response.headers.get("X-Total-Count");
  const parsed = header === null ? NaN : Number(header);
  return {
    items: response.body,
    total: Number.isNaN(parsed) ? response.body.length : parsed,
  };
}
