import type { components } from "./types";
import { apiRequest } from "./client";

export type AdminUserResponse = components["schemas"]["AdminUserResponse"];
export type AdminInviteCreateRequest =
  components["schemas"]["AdminInviteCreateRequest"];
export type AdminInviteResponse = components["schemas"]["AdminInviteResponse"];
export type AuditLogResponse = components["schemas"]["AuditLogResponse"];
export type ReportCreateRequest = components["schemas"]["ReportCreateRequest"];
export type ReportResponse = components["schemas"]["ReportResponse"];
export type ReportUpdateRequest = components["schemas"]["ReportUpdateRequest"];
export type SettingResponse = components["schemas"]["SettingResponse"];
export type SettingUpdateRequest =
  components["schemas"]["SettingUpdateRequest"];
export type BulkUserActionRequest =
  components["schemas"]["BulkUserActionRequest"];
export type BulkUserActionResponse =
  components["schemas"]["BulkUserActionResponse"];
export type SyncTagsRequest = components["schemas"]["SyncTagsRequest"];
export type SyncTagsResponse = components["schemas"]["SyncTagsResponse"];
export type RehashAudioRequest = components["schemas"]["RehashAudioRequest"];
export type ProvisionFederationKeysRequest =
  components["schemas"]["ProvisionFederationKeysRequest"];
export type PruneRemoteActivitiesRequest =
  components["schemas"]["PruneRemoteActivitiesRequest"];
export type EnrichImagesRequest = components["schemas"]["EnrichImagesRequest"];
export type EnrichImagesResponse =
  components["schemas"]["EnrichImagesResponse"];
export type NotificationsPurgeResponse =
  components["schemas"]["NotificationsPurgeResponse"];
export type AdminTaskQueuedResponse =
  components["schemas"]["AdminTaskQueuedResponse"];
export type CeleryTaskInfo = components["schemas"]["CeleryTaskInfo"];
export type CeleryQueueStats = components["schemas"]["CeleryQueueStats"];
export type CeleryTaskNameCount = components["schemas"]["CeleryTaskNameCount"];
export type CeleryTerminateRequest =
  components["schemas"]["CeleryTerminateRequest"];
export type CeleryTerminateResponse =
  components["schemas"]["CeleryTerminateResponse"];

interface StorageBackendStats {
  backend: string;
  count: number;
  size: number;
}

export interface AdminStats {
  users?: {
    total_users?: number;
    active_users?: number;
    users_by_role?: Record<string, number>;
    recent_registrations?: number;
  };
  content?: {
    total_tracks?: number;
    total_albums?: number;
    total_playlists?: number;
    total_libraries?: number;
  };
  storage?: {
    total_files?: number;
    total_size_bytes?: number;
    files_by_backend?: StorageBackendStats[];
  };
  federation?: {
    enabled?: boolean;
    instance_domain?: string;
    instance_name?: string;
  };
  celery?: {
    available?: boolean;
    worker_count?: number;
    workers?: string[];
    active_tasks?: number;
    scheduled_tasks?: number;
    reserved_tasks?: number;
    registered_task_count?: number;
    registered_tasks?: string[];
    total_tasks_processed?: number;
    error?: string;
  };
}

export function getStats(): Promise<AdminStats> {
  return apiRequest<AdminStats>("/admin/stats");
}

export function listUsers(params?: {
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<AdminUserResponse[]> {
  const query: Record<string, string | number | boolean | undefined | null> = {
    limit: params?.limit,
    offset: params?.offset,
  };
  const q = params?.q?.trim();
  if (q) {
    query.q = q;
  }
  return apiRequest<AdminUserResponse[]>("/admin/users", { query });
}

export function promoteUser(userId: string): Promise<AdminUserResponse> {
  return apiRequest<AdminUserResponse>(`/admin/users/${userId}/promote`, {
    method: "POST",
  });
}

export function demoteUser(userId: string): Promise<AdminUserResponse> {
  return apiRequest<AdminUserResponse>(`/admin/users/${userId}/demote`, {
    method: "POST",
  });
}

export function approveUser(userId: string): Promise<AdminUserResponse> {
  return apiRequest<AdminUserResponse>(`/admin/users/${userId}/approve`, {
    method: "POST",
  });
}

export function activateUser(userId: string): Promise<AdminUserResponse> {
  return apiRequest<AdminUserResponse>(`/admin/users/${userId}/activate`, {
    method: "POST",
  });
}

export function deactivateUser(userId: string): Promise<AdminUserResponse> {
  return apiRequest<AdminUserResponse>(`/admin/users/${userId}/deactivate`, {
    method: "POST",
  });
}

export function deleteUser(userId: string, recursive = false): Promise<void> {
  return apiRequest<void>(`/admin/users/${userId}`, {
    method: "DELETE",
    query: { recursive },
  });
}

export function bulkUserAction(
  body: BulkUserActionRequest,
): Promise<BulkUserActionResponse> {
  return apiRequest<BulkUserActionResponse>("/admin/users/bulk", {
    method: "POST",
    body,
  });
}

export function listSettings(): Promise<SettingResponse[]> {
  return apiRequest<SettingResponse[]>("/admin/settings");
}

export function updateSetting(
  key: string,
  value: unknown,
): Promise<SettingResponse> {
  return apiRequest<SettingResponse>(`/admin/settings/${key}`, {
    method: "PUT",
    body: { value },
  });
}

export function listReports(params?: {
  status?: string;
  target_type?: string;
  limit?: number;
  offset?: number;
}): Promise<ReportResponse[]> {
  return apiRequest<ReportResponse[]>("/admin/reports/", { query: params });
}

export function updateReport(
  reportId: string,
  body: ReportUpdateRequest,
): Promise<ReportResponse> {
  return apiRequest<ReportResponse>(`/admin/reports/${reportId}`, {
    method: "PUT",
    body,
  });
}

// ``createReport`` lives in ``./reports`` (user-facing, not admin-only);
// re-exported here so existing imports keep working.
export { createReport } from "./reports";

export function listInvites(params?: {
  limit?: number;
  offset?: number;
}): Promise<AdminInviteResponse[]> {
  return apiRequest<AdminInviteResponse[]>("/admin/invites", { query: params });
}

export function createInvite(
  body: AdminInviteCreateRequest,
): Promise<AdminInviteResponse> {
  return apiRequest<AdminInviteResponse>("/admin/invites", {
    method: "POST",
    body,
  });
}

export function deleteInvite(code: string): Promise<void> {
  return apiRequest<void>(`/admin/invites/${code}`, { method: "DELETE" });
}

export function listAuditLogs(params?: {
  action?: string;
  actor_id?: string;
  target_type?: string;
  limit?: number;
  offset?: number;
}): Promise<AuditLogResponse[]> {
  return apiRequest<AuditLogResponse[]>("/admin/audit", { query: params });
}

export function listAuditTargetTypes(): Promise<string[]> {
  return apiRequest<string[]>("/admin/audit/target-types");
}

export function triggerStorageCleanup(): Promise<unknown> {
  return apiRequest<unknown>("/admin/storage/cleanup", { method: "POST" });
}

export function syncTags(body: SyncTagsRequest): Promise<SyncTagsResponse> {
  return apiRequest<SyncTagsResponse>("/admin/sync-tags", {
    method: "POST",
    body,
  });
}

export function rehashAudio(
  body: RehashAudioRequest = { dry_run: false },
): Promise<AdminTaskQueuedResponse> {
  return apiRequest<AdminTaskQueuedResponse>("/admin/rehash-audio", {
    method: "POST",
    body,
  });
}

export function provisionFederationKeys(
  body: ProvisionFederationKeysRequest = { dry_run: false },
): Promise<AdminTaskQueuedResponse> {
  return apiRequest<AdminTaskQueuedResponse>(
    "/admin/provision-federation-keys",
    {
      method: "POST",
      body,
    },
  );
}

export function pruneRemoteActivities(
  body: PruneRemoteActivitiesRequest = { dry_run: false },
): Promise<AdminTaskQueuedResponse> {
  return apiRequest<AdminTaskQueuedResponse>(
    "/admin/federation/prune-remote-activities",
    {
      method: "POST",
      body,
    },
  );
}

export function listCeleryTasks(): Promise<CeleryTaskInfo[]> {
  return apiRequest<CeleryTaskInfo[]>("/admin/celery/tasks");
}

export function getCeleryQueueStats(): Promise<CeleryQueueStats> {
  return apiRequest<CeleryQueueStats>("/admin/celery/queue");
}

export function terminateCeleryTasks(
  body: CeleryTerminateRequest,
): Promise<CeleryTerminateResponse> {
  return apiRequest<CeleryTerminateResponse>("/admin/celery/terminate", {
    method: "POST",
    body,
  });
}

export function enrichImages(
  body: EnrichImagesRequest,
): Promise<EnrichImagesResponse> {
  return apiRequest<EnrichImagesResponse>("/admin/enrich-images", {
    method: "POST",
    body,
  });
}

export function purgeNotifications(): Promise<NotificationsPurgeResponse> {
  return apiRequest<NotificationsPurgeResponse>("/admin/notifications/purge", {
    method: "POST",
  });
}

export type AdminUserModerationAction = "limit" | "suspend";
export type AdminInstanceModerationAction = "defederate" | "followers_only";

export type AdminModeratedActor =
  components["schemas"]["AdminModeratedActorResponse"];
export type AdminModeratedInstance =
  components["schemas"]["AdminModeratedInstanceResponse"];

/** List actors under an admin limit/suspend action. */
export function listModeratedUsers(params?: {
  action?: AdminUserModerationAction;
  limit?: number;
  offset?: number;
}): Promise<AdminModeratedActor[]> {
  return apiRequest<AdminModeratedActor[]>("/admin/moderation/users", {
    query: params,
  });
}

/** Limit or suspend a local user or remote actor. */
export function moderateUser(body: {
  actor_url: string;
  action: AdminUserModerationAction;
  reason?: string | null;
}): Promise<AdminModeratedActor> {
  return apiRequest<AdminModeratedActor>("/admin/moderation/users", {
    method: "POST",
    body,
  });
}

/** Remove an admin limit/suspend on an actor. */
export function unmoderateUser(actorUrl: string): Promise<void> {
  return apiRequest<void>("/admin/moderation/users", {
    method: "DELETE",
    body: { actor_url: actorUrl },
  });
}

/** List domains under an admin moderation policy. */
export function listModeratedInstances(params?: {
  action?: AdminInstanceModerationAction;
  limit?: number;
  offset?: number;
}): Promise<AdminModeratedInstance[]> {
  return apiRequest<AdminModeratedInstance[]>("/admin/moderation/instances", {
    query: params,
  });
}

/** Defederate or followers-only-limit a remote domain. */
export function moderateInstance(body: {
  domain: string;
  action: AdminInstanceModerationAction;
  reason?: string | null;
}): Promise<AdminModeratedInstance> {
  return apiRequest<AdminModeratedInstance>("/admin/moderation/instances", {
    method: "POST",
    body,
  });
}

/** Remove an admin domain moderation policy. */
export function unmoderateInstance(domain: string): Promise<void> {
  return apiRequest<void>("/admin/moderation/instances", {
    method: "DELETE",
    body: { domain },
  });
}
