import { apiRequest, apiRequestWithHeaders } from "./client";
import type { components } from "./types";

export type TagSummary = components["schemas"]["TagSummaryResponse"];
export type TaggedItem = components["schemas"]["TaggedItemResponse"];
export type TagListRequest = components["schemas"]["TagListRequest"];

export interface ListTagsParams {
  [key: string]: string | number | undefined;
  q?: string;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}

export interface ListTagsResult {
  items: TagSummary[];
  total: number;
  offset: number;
}

export interface ListTagItemsParams {
  [key: string]: string | number | undefined;
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  type?: TaggedItemType;
}

export interface ListTagItemsResult {
  items: TaggedItem[];
  total: number;
  offset: number;
}

export type EntityType =
  "tracks" | "albums" | "artists" | "playlists" | "libraries";
export type TaggedItemType =
  "artist" | "album" | "track" | "playlist" | "library";

export async function listTags(
  params?: ListTagsParams,
): Promise<ListTagsResult> {
  const response = await apiRequestWithHeaders<TagSummary[]>("/tags", {
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

export async function listTagItems(
  tag: string,
  params?: ListTagItemsParams,
): Promise<ListTagItemsResult> {
  const response = await apiRequestWithHeaders<TaggedItem[]>(
    `/tags/${encodeURIComponent(tag)}`,
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

export function deleteTag(tag: string): Promise<unknown> {
  return apiRequest<unknown>(`/tags/${encodeURIComponent(tag)}`, {
    method: "DELETE",
  });
}

export function addTags(
  type: EntityType,
  id: string,
  body: TagListRequest,
): Promise<unknown> {
  return apiRequest<unknown>(`/${type}/${id}/tags`, {
    method: "POST",
    body,
  });
}

export function removeTag(
  type: EntityType,
  id: string,
  tag: string,
): Promise<unknown> {
  return apiRequest<unknown>(`/${type}/${id}/tags/${encodeURIComponent(tag)}`, {
    method: "DELETE",
  });
}

export async function listUserTags(
  userId: string,
  params?: ListTagsParams,
): Promise<ListTagsResult> {
  const response = await apiRequestWithHeaders<TagSummary[]>(
    `/users/${userId}/tags`,
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

export async function listUserTagItems(
  userId: string,
  tag: string,
  params?: ListTagItemsParams,
): Promise<ListTagItemsResult> {
  const response = await apiRequestWithHeaders<TaggedItem[]>(
    `/users/${userId}/tags/${encodeURIComponent(tag)}`,
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
