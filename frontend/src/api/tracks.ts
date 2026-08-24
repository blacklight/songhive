import type { components } from "./types";
import { apiRequest } from "./client";

export type TrackResponse = components["schemas"]["TrackResponse"];
export type TrackUpdate = components["schemas"]["TrackUpdate"];

export function listTracks(params?: {
  q?: string;
  artist_id?: string;
  album_id?: string;
  genre?: string;
  year_from?: number;
  year_to?: number;
  library_id?: string;
  limit?: number;
  offset?: number;
}): Promise<TrackResponse[]> {
  return apiRequest<TrackResponse[]>("/tracks", { query: params });
}

export function getTrack(id: string): Promise<TrackResponse> {
  return apiRequest<TrackResponse>(`/tracks/${id}`);
}

export function updateTrack(
  id: string,
  body: TrackUpdate,
): Promise<TrackResponse> {
  return apiRequest<TrackResponse>(`/tracks/${id}`, { method: "PATCH", body });
}

export function deleteTrack(id: string): Promise<void> {
  return apiRequest<void>(`/tracks/${id}`, { method: "DELETE" });
}
