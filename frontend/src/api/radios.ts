import type { components } from "./types";
import { apiRequest } from "./client";
import type { TrackResponse } from "./tracks";
import type { Visibility } from "./libraries";

export type RadioResponse = components["schemas"]["RadioResponse"];
export type RadioCreate = components["schemas"]["RadioCreate"];

// Re-export Visibility so call-sites can import it from @/api/radios.
export type { Visibility } from "./libraries";

export function listRadios(params?: {
  limit?: number;
  offset?: number;
}): Promise<RadioResponse[]> {
  return apiRequest<RadioResponse[]>("/radios/", { query: params });
}

export function createRadio(
  body: RadioCreate,
  visibility?: Visibility,
): Promise<RadioResponse> {
  return apiRequest<RadioResponse>("/radios/", {
    method: "POST",
    body,
    query: { visibility },
  });
}

export function getRadio(radioId: string): Promise<RadioResponse> {
  return apiRequest<RadioResponse>(`/radios/${radioId}`);
}

export function getRadioTracks(
  radioId: string,
  params?: { count?: number },
): Promise<TrackResponse[]> {
  // The generated OpenAPI type marks this response as `unknown`.
  // We assume the runtime payload is a list of track objects.
  return apiRequest<unknown>(`/radios/${radioId}/tracks`, {
    query: params,
  }).then((r) => r as TrackResponse[]);
}
