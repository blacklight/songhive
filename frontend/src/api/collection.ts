import type { components } from "./types";
import { apiRequest } from "./client";

export type CollectionItemResponse =
  components["schemas"]["CollectionItemResponse"];

export type CollectionItemType =
  "album" | "artist" | "library" | "playlist" | "radio" | "track";

export function listCollection(params?: {
  item_type?: CollectionItemType;
  limit?: number;
  offset?: number;
}): Promise<CollectionItemResponse[]> {
  return apiRequest<CollectionItemResponse[]>("/collection/", {
    query: params,
  });
}

export function addToCollection(
  itemType: CollectionItemType,
  itemId: string,
): Promise<CollectionItemResponse> {
  return apiRequest<CollectionItemResponse>(
    `/collection/${itemType}/${itemId}`,
    {
      method: "POST",
    },
  );
}

export function removeFromCollection(
  itemType: CollectionItemType,
  itemId: string,
): Promise<void> {
  return apiRequest<void>(`/collection/${itemType}/${itemId}`, {
    method: "DELETE",
  });
}
