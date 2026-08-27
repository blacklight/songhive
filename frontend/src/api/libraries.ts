import type { components } from "./types";
import { apiRequest } from "./client";

export type LibraryResponse = components["schemas"]["LibraryResponse"];
export type LibraryCreate = components["schemas"]["LibraryCreate"];
export type LibraryUpdate = components["schemas"]["LibraryUpdate"];
export type ScanRequest = components["schemas"]["ScanRequest"];

export type TrackResponse = components["schemas"]["TrackResponse"];
export type Visibility = components["schemas"]["Visibility"];

export function listLibraries(params?: {
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
}): Promise<LibraryResponse[]> {
  return apiRequest<LibraryResponse[]>("/libraries/", { query: params });
}

export function createLibrary(
  body: LibraryCreate,
  params?: { visibility?: Visibility },
): Promise<LibraryResponse> {
  return apiRequest<LibraryResponse>("/libraries/", {
    method: "POST",
    body,
    query: params,
  });
}

export function getLibrary(id: string): Promise<LibraryResponse> {
  return apiRequest<LibraryResponse>(`/libraries/${id}`);
}

export function updateLibrary(
  id: string,
  body: LibraryUpdate,
): Promise<LibraryResponse> {
  return apiRequest<LibraryResponse>(`/libraries/${id}`, {
    method: "PATCH",
    body,
  });
}

export function deleteLibrary(id: string, recursive = false): Promise<void> {
  return apiRequest<void>(`/libraries/${id}`, {
    method: "DELETE",
    query: { recursive },
  });
}

export function uploadLibraryImage(
  id: string,
  file: File,
): Promise<LibraryResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<LibraryResponse>(`/libraries/${id}/image`, {
    method: "POST",
    body,
  });
}

export function deleteLibraryImage(id: string): Promise<LibraryResponse> {
  return apiRequest<LibraryResponse>(`/libraries/${id}/image`, {
    method: "DELETE",
  });
}

export function uploadLibraryCover(
  id: string,
  file: File,
): Promise<LibraryResponse> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<LibraryResponse>(`/libraries/${id}/cover`, {
    method: "POST",
    body,
  });
}

export function deleteLibraryCover(id: string): Promise<LibraryResponse> {
  return apiRequest<LibraryResponse>(`/libraries/${id}/cover`, {
    method: "DELETE",
  });
}

export function listLibraryTracks(
  id: string,
  params?: {
    limit?: number;
    offset?: number;
    include?: string;
    sort_by?: string;
    sort_dir?: "asc" | "desc";
  },
): Promise<TrackResponse[]> {
  return apiRequest<TrackResponse[]>(`/libraries/${id}/tracks`, {
    query: params,
  });
}

export function uploadTrack(
  id: string,
  file: File,
  params?: { force?: boolean; visibility?: Visibility; enrich?: boolean },
): Promise<unknown> {
  const body = new FormData();
  body.append("file", file);
  return apiRequest<unknown>(`/libraries/${id}/tracks`, {
    method: "POST",
    query: params,
    body,
  });
}

export function bulkUploadTracks(
  id: string,
  files: File[],
  params?: { force?: boolean; visibility?: Visibility; enrich?: boolean },
): Promise<unknown> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  return apiRequest<unknown>(`/libraries/${id}/tracks/bulk`, {
    method: "POST",
    query: params,
    body,
  });
}

export function scanLibrary(id: string, body: ScanRequest): Promise<unknown> {
  return apiRequest<unknown>(`/libraries/${id}/scan`, {
    method: "POST",
    body,
  });
}

export interface AddTracksToLibraryRequest {
  track_ids?: string[];
  album_id?: string;
  artist_id?: string;
}

export interface AddTracksToLibraryResponse {
  added: number;
  track_ids: string[];
}

export function addTracksToLibrary(
  id: string,
  body: AddTracksToLibraryRequest,
): Promise<AddTracksToLibraryResponse> {
  return apiRequest<AddTracksToLibraryResponse>(`/libraries/${id}/tracks/add`, {
    method: "POST",
    body,
  });
}

export interface RemoveTracksFromLibraryRequest {
  track_ids: string[];
}

export interface RemoveTracksFromLibraryResponse {
  removed: number;
  track_ids: string[];
}

export function removeTracksFromLibrary(
  id: string,
  body: RemoveTracksFromLibraryRequest,
): Promise<RemoveTracksFromLibraryResponse> {
  return apiRequest<RemoveTracksFromLibraryResponse>(
    `/libraries/${id}/tracks/remove`,
    {
      method: "POST",
      body,
    },
  );
}
