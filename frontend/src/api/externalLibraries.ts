import { apiRequest, apiRequestWithHeaders } from "./client";
import type { components } from "./types";

export type ExternalLibraryResponse =
  components["schemas"]["ExternalLibraryResponse"];
export type ExternalLibraryCreate =
  components["schemas"]["ExternalLibraryCreate"];
export type ExternalLibraryUpdate =
  components["schemas"]["ExternalLibraryUpdate"];
export type ExternalSyncRunResponse =
  components["schemas"]["ExternalSyncRunResponse"];
export type ExternalSyncRequest = components["schemas"]["ExternalSyncRequest"];
export type ExternalTrackResponse =
  components["schemas"]["ExternalTrackResponse"];
export type ExternalTrackDeleteRequest =
  components["schemas"]["ExternalTrackDeleteRequest"];
export type ExternalProviderResponse =
  components["schemas"]["ExternalProviderResponse"];
export type ExternalDuplicateWarning =
  components["schemas"]["ExternalDuplicateWarning"];
export type ExternalDuplicateResolutionRequest =
  components["schemas"]["ExternalDuplicateResolutionRequest"];
export type BulkExternalTrackDeleteRequest =
  components["schemas"]["BulkExternalTrackDeleteRequest"];

export interface ListExternalLibrariesResult {
  libraries: ExternalLibraryResponse[];
  total: number;
}

export interface ListExternalSyncRunsResult {
  syncRuns: ExternalSyncRunResponse[];
  total: number;
}

export interface ListExternalTracksResult {
  tracks: ExternalTrackResponse[];
  total: number;
}

function getTotalHeader(headers: Headers): number | undefined {
  const value = headers.get("X-Total-Count");
  if (!value) return undefined;
  const parsed = parseInt(value, 10);
  return Number.isNaN(parsed) ? undefined : parsed;
}

export function listUserProviders(): Promise<ExternalProviderResponse[]> {
  return apiRequest<ExternalProviderResponse[]>(
    "/external-libraries/providers",
  );
}

export async function listUserExternalLibraries(params?: {
  limit?: number;
  offset?: number;
}): Promise<ListExternalLibrariesResult> {
  const response = await apiRequestWithHeaders<ExternalLibraryResponse[]>(
    "/external-libraries/",
    { query: params },
  );
  return {
    libraries: response.body,
    total: getTotalHeader(response.headers) ?? response.body.length,
  };
}

export function createUserExternalLibrary(
  body: ExternalLibraryCreate,
): Promise<ExternalLibraryResponse> {
  return apiRequest<ExternalLibraryResponse>("/external-libraries/", {
    method: "POST",
    body,
  });
}

export function getUserExternalLibrary(
  id: string,
): Promise<ExternalLibraryResponse> {
  return apiRequest<ExternalLibraryResponse>(`/external-libraries/${id}`);
}

export function updateUserExternalLibrary(
  id: string,
  body: ExternalLibraryUpdate,
): Promise<ExternalLibraryResponse> {
  return apiRequest<ExternalLibraryResponse>(`/external-libraries/${id}`, {
    method: "PATCH",
    body,
  });
}

export function deleteUserExternalLibrary(id: string): Promise<void> {
  return apiRequest<void>(`/external-libraries/${id}`, { method: "DELETE" });
}

export function syncUserExternalLibrary(
  id: string,
  body: ExternalSyncRequest = { include_tombstones: false },
): Promise<{ sync_run_id: string }> {
  return apiRequest<{ sync_run_id: string }>(`/external-libraries/${id}/sync`, {
    method: "POST",
    body,
  });
}

export async function listUserSyncRuns(
  externalLibraryId: string,
  params?: { limit?: number; offset?: number },
): Promise<ListExternalSyncRunsResult> {
  const response = await apiRequestWithHeaders<ExternalSyncRunResponse[]>(
    `/external-libraries/${externalLibraryId}/sync-runs`,
    { query: params },
  );
  return {
    syncRuns: response.body,
    total: getTotalHeader(response.headers) ?? response.body.length,
  };
}

export async function listUserExternalTracks(
  externalLibraryId: string,
  params?: {
    state?: string;
    limit?: number;
    offset?: number;
  },
): Promise<ListExternalTracksResult> {
  const response = await apiRequestWithHeaders<ExternalTrackResponse[]>(
    `/external-libraries/${externalLibraryId}/tracks`,
    { query: params },
  );
  return {
    tracks: response.body,
    total: getTotalHeader(response.headers) ?? response.body.length,
  };
}

export function restoreUserExternalTrack(
  externalLibraryId: string,
  externalTrackId: string,
): Promise<ExternalTrackResponse> {
  return apiRequest<ExternalTrackResponse>(
    `/external-libraries/${externalLibraryId}/tracks/${externalTrackId}/restore`,
    { method: "POST" },
  );
}

export function deleteUserExternalTrack(
  externalLibraryId: string,
  externalTrackId: string,
  body: ExternalTrackDeleteRequest = {
    delete_source: false,
    remove_songhive_track: false,
  },
): Promise<void> {
  return apiRequest<void>(
    `/external-libraries/${externalLibraryId}/tracks/${externalTrackId}`,
    { method: "DELETE", body },
  );
}

export function listAdminProviders(): Promise<ExternalProviderResponse[]> {
  return apiRequest<ExternalProviderResponse[]>(
    "/admin/external-libraries/providers",
  );
}

export async function adminListExternalLibraries(params?: {
  include_user?: boolean;
  limit?: number;
  offset?: number;
}): Promise<ListExternalLibrariesResult> {
  const response = await apiRequestWithHeaders<ExternalLibraryResponse[]>(
    "/admin/external-libraries/",
    { query: params },
  );
  return {
    libraries: response.body,
    total: getTotalHeader(response.headers) ?? response.body.length,
  };
}

export function adminCreateExternalLibrary(
  body: ExternalLibraryCreate,
): Promise<ExternalLibraryResponse> {
  return apiRequest<ExternalLibraryResponse>("/admin/external-libraries/", {
    method: "POST",
    body,
  });
}

export function adminGetExternalLibrary(
  id: string,
): Promise<ExternalLibraryResponse> {
  return apiRequest<ExternalLibraryResponse>(`/admin/external-libraries/${id}`);
}

export function adminUpdateExternalLibrary(
  id: string,
  body: ExternalLibraryUpdate,
): Promise<ExternalLibraryResponse> {
  return apiRequest<ExternalLibraryResponse>(
    `/admin/external-libraries/${id}`,
    {
      method: "PATCH",
      body,
    },
  );
}

export function adminDeleteExternalLibrary(id: string): Promise<void> {
  return apiRequest<void>(`/admin/external-libraries/${id}`, {
    method: "DELETE",
  });
}

export function adminSyncExternalLibrary(
  id: string,
  body: ExternalSyncRequest = { include_tombstones: false },
): Promise<{ sync_run_id: string }> {
  return apiRequest<{ sync_run_id: string }>(
    `/admin/external-libraries/${id}/sync`,
    { method: "POST", body },
  );
}

export async function adminListExternalSyncRuns(
  externalLibraryId: string,
  params?: { limit?: number; offset?: number },
): Promise<ListExternalSyncRunsResult> {
  const response = await apiRequestWithHeaders<ExternalSyncRunResponse[]>(
    `/admin/external-libraries/${externalLibraryId}/sync-runs`,
    { query: params },
  );
  return {
    syncRuns: response.body,
    total: getTotalHeader(response.headers) ?? response.body.length,
  };
}

export async function adminListExternalTracks(
  externalLibraryId: string,
  params?: {
    state?: string;
    limit?: number;
    offset?: number;
  },
): Promise<ListExternalTracksResult> {
  const response = await apiRequestWithHeaders<ExternalTrackResponse[]>(
    `/admin/external-libraries/${externalLibraryId}/tracks`,
    { query: params },
  );
  return {
    tracks: response.body,
    total: getTotalHeader(response.headers) ?? response.body.length,
  };
}

export function adminRestoreExternalTrack(
  externalLibraryId: string,
  externalTrackId: string,
): Promise<ExternalTrackResponse> {
  return apiRequest<ExternalTrackResponse>(
    `/admin/external-libraries/${externalLibraryId}/tracks/${externalTrackId}/restore`,
    { method: "POST" },
  );
}

export function adminDeleteExternalTrack(
  externalLibraryId: string,
  externalTrackId: string,
  body: ExternalTrackDeleteRequest = {
    delete_source: false,
    remove_songhive_track: false,
  },
): Promise<void> {
  return apiRequest<void>(
    `/admin/external-libraries/${externalLibraryId}/tracks/${externalTrackId}`,
    { method: "DELETE", body },
  );
}

export function adminBulkDeleteExternalTracks(
  externalLibraryId: string,
  body: BulkExternalTrackDeleteRequest,
): Promise<void> {
  return apiRequest<void>(
    `/admin/external-libraries/${externalLibraryId}/tracks/bulk-delete`,
    { method: "POST", body },
  );
}

export type ExternalDuplicateResolutionResponse =
  | components["schemas"]["StoredFileResponse"]
  | components["schemas"]["TrackResponse"];

export function resolveUploadDuplicate(
  token: string,
  action: "keep_local" | "discard_upload",
): Promise<ExternalDuplicateResolutionResponse> {
  const body: ExternalDuplicateResolutionRequest = { token, action };
  return apiRequest<ExternalDuplicateResolutionResponse>(
    "/files/upload/resolve-duplicate",
    { method: "POST", body },
  );
}
