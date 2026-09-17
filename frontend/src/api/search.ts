import { apiRequest } from "./client";
import type { components } from "./types";

export type SearchEntity =
  components["schemas"]["SearchResultSection"]["entity"];
export type SearchResultItem = components["schemas"]["SearchResultItem"];
export type SearchResultSection = components["schemas"]["SearchResultSection"];
export type SearchResponse = components["schemas"]["SearchResponse"] & {
  // Whether this caller may run explicit remote lookups via
  // ``/remote/lookup`` — drives the "search the fediverse" affordance.
  remote_available?: boolean;
};

export function searchPreview(
  q: string,
  entities?: SearchEntity[],
  limit = 5,
  options?: { remoteUsers?: boolean; includeRemote?: boolean },
): Promise<SearchResponse> {
  const params: Record<string, string | number | boolean | undefined | null> = {
    q,
    limit,
  };
  if (entities && entities.length) {
    params.entities = entities.join(",");
  }
  if (options?.remoteUsers) {
    params.remote_users = true;
  }
  if (options?.includeRemote === false) {
    params.include_remote = false;
  }
  return apiRequest<SearchResponse>("/search/", { query: params });
}
