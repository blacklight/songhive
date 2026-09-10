import { apiRequest } from "./client";
import type { components } from "./types";

export type SearchEntity =
  components["schemas"]["SearchResultSection"]["entity"];
export type SearchResultItem = components["schemas"]["SearchResultItem"];
export type SearchResultSection = components["schemas"]["SearchResultSection"];
export type SearchResponse = components["schemas"]["SearchResponse"];

export function searchPreview(
  q: string,
  entities?: SearchEntity[],
  limit = 5,
): Promise<SearchResponse> {
  const params: Record<string, string | number | boolean | undefined | null> = {
    q,
    limit,
  };
  if (entities && entities.length) {
    params.entities = entities.join(",");
  }
  return apiRequest<SearchResponse>("/search/", { query: params });
}
