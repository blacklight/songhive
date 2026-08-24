import type { components } from "./types";
import { apiRequest } from "./client";

export type FavoriteResponse = components["schemas"]["FavoriteResponse"];

export function listFavorites(params?: {
  limit?: number;
  offset?: number;
}): Promise<FavoriteResponse[]> {
  return apiRequest<FavoriteResponse[]>("/favorites", { query: params });
}

export function addFavorite(trackId: string): Promise<FavoriteResponse> {
  return apiRequest<FavoriteResponse>(`/favorites/${trackId}`, {
    method: "POST",
  });
}

export function removeFavorite(trackId: string): Promise<void> {
  return apiRequest<void>(`/favorites/${trackId}`, { method: "DELETE" });
}
