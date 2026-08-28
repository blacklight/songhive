import { apiRequest, apiRequestWithHeaders } from "./client";
import type { components } from "./types";

export type HashtagSummary = components["schemas"]["HashtagSummaryResponse"];
export type TaggedItem = components["schemas"]["TaggedItemResponse"];
export type HashtagListRequest = components["schemas"]["HashtagListRequest"];

export interface ListHashtagsParams {
  [key: string]: string | number | undefined;
  q?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ListHashtagsResult {
  items: HashtagSummary[];
  total: number;
  offset: number;
}

export interface ListHashtagItemsParams {
  [key: string]: string | number | undefined;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ListHashtagItemsResult {
  items: TaggedItem[];
  total: number;
  offset: number;
}

export type EntityType =
  "tracks" | "albums" | "artists" | "playlists" | "libraries";

export async function listHashtags(
  params?: ListHashtagsParams,
): Promise<ListHashtagsResult> {
  const response = await apiRequestWithHeaders<HashtagSummary[]>("/hashtags", {
    query: params,
  });
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}

export async function listHashtagItems(
  hashtag: string,
  params?: ListHashtagItemsParams,
): Promise<ListHashtagItemsResult> {
  const response = await apiRequestWithHeaders<TaggedItem[]>(
    `/hashtags/${encodeURIComponent(hashtag)}`,
    { query: params },
  );
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}

export function deleteHashtag(hashtag: string): Promise<unknown> {
  return apiRequest<unknown>(`/hashtags/${encodeURIComponent(hashtag)}`, {
    method: "DELETE",
  });
}

export function addHashtags(
  type: EntityType,
  id: string,
  body: HashtagListRequest,
): Promise<unknown> {
  return apiRequest<unknown>(`/${type}/${id}/hashtags`, {
    method: "POST",
    body,
  });
}

export function removeHashtag(
  type: EntityType,
  id: string,
  hashtag: string,
): Promise<unknown> {
  return apiRequest<unknown>(
    `/${type}/${id}/hashtags/${encodeURIComponent(hashtag)}`,
    { method: "DELETE" },
  );
}

export async function listUserHashtags(
  userId: string,
  params?: ListHashtagsParams,
): Promise<ListHashtagsResult> {
  const response = await apiRequestWithHeaders<HashtagSummary[]>(
    `/users/${userId}/hashtags`,
    { query: params },
  );
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}

export async function listUserHashtagItems(
  userId: string,
  hashtag: string,
  params?: ListHashtagItemsParams,
): Promise<ListHashtagItemsResult> {
  const response = await apiRequestWithHeaders<TaggedItem[]>(
    `/users/${userId}/hashtags/${encodeURIComponent(hashtag)}`,
    { query: params },
  );
  const offsetHeader = response.headers.get("X-List-Offset");
  const totalHeader = response.headers.get("X-Total-Count");
  return {
    items: response.body,
    offset: offsetHeader ? parseInt(offsetHeader, 10) : (params?.offset ?? 0),
    total: totalHeader ? parseInt(totalHeader, 10) : response.body.length,
  };
}
