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
  return apiRequest<AdminUserResponse[]>("/admin/users", { query: params });
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

export function createReport(
  body: ReportCreateRequest,
): Promise<ReportResponse> {
  return apiRequest<ReportResponse>("/reports", { method: "POST", body });
}

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

export function triggerStorageCleanup(): Promise<unknown> {
  return apiRequest<unknown>("/admin/storage/cleanup", { method: "POST" });
}
