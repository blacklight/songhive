import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  getStats,
  listUsers,
  promoteUser,
  demoteUser,
  approveUser,
  activateUser,
  deactivateUser,
  deleteUser,
  bulkUserAction,
  listSettings,
  updateSetting,
  listReports,
  updateReport,
  createReport,
  listInvites,
  createInvite,
  deleteInvite,
  listAuditLogs,
  triggerStorageCleanup,
  syncTags,
  rehashAudio,
  provisionFederationKeys,
  type AdminUserResponse,
  type AdminInviteResponse,
  type AdminInviteCreateRequest,
  type ReportResponse,
  type ReportCreateRequest,
  type ReportUpdateRequest,
  type SettingResponse,
  type BulkUserActionRequest,
  type AuditLogResponse,
} from "./admin";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleUser: AdminUserResponse = {
  id: "user-1",
  username: "alice",
  email: "alice@example.com",
  is_active: true,
  role: "user",
};

const sampleInvite: AdminInviteResponse = {
  id: "invite-1",
  code: "ABC123",
  created_by: "admin-1",
  max_uses: 10,
  uses: 0,
  expires_at: null,
  created_at: "2024-01-01T00:00:00Z",
};

const sampleReport: ReportResponse = {
  id: "report-1",
  reporter_id: "user-1",
  target_type: "track",
  target_id: "track-1",
  reason: "inappropriate",
  description: null,
  status: "pending",
  reviewed_by: null,
  reviewed_at: null,
  resolution_notes: null,
  created_at: "2024-01-01T00:00:00Z",
};

const sampleSetting: SettingResponse = {
  key: "instance_name",
  value: "Songhive",
  type: "string",
  updated_at: null,
};

const sampleAuditLog: AuditLogResponse = {
  id: "audit-1",
  action: "user.update",
  actor_id: "admin-1",
  target_type: "user",
  target_id: "user-1",
  details: { role: "admin" },
  ip_address: "127.0.0.1",
  created_at: "2024-01-01T00:00:00Z",
};

describe("admin endpoints", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("getStats fetches /admin/stats", async () => {
    const stats = {
      users: {
        total_users: 5,
        active_users: 3,
        users_by_role: {},
        recent_registrations: 0,
      },
      content: {
        total_tracks: 1,
        total_albums: 0,
        total_playlists: 0,
        total_libraries: 0,
      },
      storage: { total_files: 0, total_size_bytes: 0 },
      federation: { enabled: false },
    };
    apiRequest.mockResolvedValueOnce(stats);
    const result = await getStats();
    expect(apiRequest).toHaveBeenCalledWith("/admin/stats");
    expect(result).toEqual(stats);
  });

  it("listUsers fetches with query params", async () => {
    apiRequest.mockResolvedValueOnce([sampleUser]);
    const result = await listUsers({ q: "alice", limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/admin/users", {
      query: { q: "alice", limit: 10, offset: 5 },
    });
    expect(result).toEqual([sampleUser]);
  });

  it("listUsers omits an empty or whitespace-only query", async () => {
    apiRequest.mockResolvedValueOnce([sampleUser]);
    const result = await listUsers({ q: "   ", limit: 25, offset: 0 });
    expect(apiRequest).toHaveBeenCalledWith("/admin/users", {
      query: { limit: 25, offset: 0 },
    });
    expect(result).toEqual([sampleUser]);
  });

  it.each([
    ["promoteUser", promoteUser, "/admin/users/user-1/promote"],
    ["demoteUser", demoteUser, "/admin/users/user-1/demote"],
    ["approveUser", approveUser, "/admin/users/user-1/approve"],
    ["activateUser", activateUser, "/admin/users/user-1/activate"],
    ["deactivateUser", deactivateUser, "/admin/users/user-1/deactivate"],
  ])("%s posts to %s", async (_name, fn, path) => {
    apiRequest.mockResolvedValueOnce(sampleUser);
    const result = await fn("user-1");
    expect(apiRequest).toHaveBeenCalledWith(path, { method: "POST" });
    expect(result).toEqual(sampleUser);
  });

  it("deleteUser sends DELETE with recursive query", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteUser("user-1", true);
    expect(apiRequest).toHaveBeenCalledWith("/admin/users/user-1", {
      method: "DELETE",
      query: { recursive: true },
    });
  });

  it("bulkUserAction sends POST with body", async () => {
    apiRequest.mockResolvedValueOnce({
      action: "deactivate",
      processed: 2,
      failed: [],
    });
    const body: BulkUserActionRequest = {
      action: "deactivate",
      user_ids: ["user-1", "user-2"],
      recursive: false,
    };
    const result = await bulkUserAction(body);
    expect(apiRequest).toHaveBeenCalledWith("/admin/users/bulk", {
      method: "POST",
      body,
    });
    expect(result.processed).toBe(2);
  });

  it("listSettings fetches /admin/settings", async () => {
    apiRequest.mockResolvedValueOnce([sampleSetting]);
    const result = await listSettings();
    expect(apiRequest).toHaveBeenCalledWith("/admin/settings");
    expect(result).toEqual([sampleSetting]);
  });

  it("updateSetting sends PUT with value", async () => {
    apiRequest.mockResolvedValueOnce(sampleSetting);
    const result = await updateSetting("instance_name", "New Name");
    expect(apiRequest).toHaveBeenCalledWith("/admin/settings/instance_name", {
      method: "PUT",
      body: { value: "New Name" },
    });
    expect(result).toEqual(sampleSetting);
  });

  it("listReports targets /admin/reports/ with trailing slash", async () => {
    apiRequest.mockResolvedValueOnce([sampleReport]);
    const result = await listReports({
      status: "pending",
      target_type: "track",
      limit: 10,
      offset: 0,
    });
    expect(apiRequest).toHaveBeenCalledWith("/admin/reports/", {
      query: { status: "pending", target_type: "track", limit: 10, offset: 0 },
    });
    expect(result).toEqual([sampleReport]);
  });

  it("updateReport sends PUT", async () => {
    apiRequest.mockResolvedValueOnce(sampleReport);
    const body: ReportUpdateRequest = {
      status: "resolved",
      resolution_notes: "Resolved",
    };
    const result = await updateReport("report-1", body);
    expect(apiRequest).toHaveBeenCalledWith("/admin/reports/report-1", {
      method: "PUT",
      body,
    });
    expect(result).toEqual(sampleReport);
  });

  it("createReport posts to /reports without /admin prefix", async () => {
    apiRequest.mockResolvedValueOnce(sampleReport);
    const body: ReportCreateRequest = {
      target_type: "track",
      target_id: "track-1",
      reason: "spam",
    };
    const result = await createReport(body);
    expect(apiRequest).toHaveBeenCalledWith("/reports", {
      method: "POST",
      body,
    });
    expect(result).toEqual(sampleReport);
  });

  it("listInvites fetches with pagination", async () => {
    apiRequest.mockResolvedValueOnce([sampleInvite]);
    const result = await listInvites({ limit: 10, offset: 0 });
    expect(apiRequest).toHaveBeenCalledWith("/admin/invites", {
      query: { limit: 10, offset: 0 },
    });
    expect(result).toEqual([sampleInvite]);
  });

  it("createInvite posts to /admin/invites", async () => {
    apiRequest.mockResolvedValueOnce(sampleInvite);
    const body: AdminInviteCreateRequest = { max_uses: 5, expires_at: null };
    const result = await createInvite(body);
    expect(apiRequest).toHaveBeenCalledWith("/admin/invites", {
      method: "POST",
      body,
    });
    expect(result).toEqual(sampleInvite);
  });

  it("deleteInvite sends DELETE", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteInvite("ABC123");
    expect(apiRequest).toHaveBeenCalledWith("/admin/invites/ABC123", {
      method: "DELETE",
    });
  });

  it("listAuditLogs fetches with filters", async () => {
    apiRequest.mockResolvedValueOnce([sampleAuditLog]);
    const result = await listAuditLogs({
      action: "user.update",
      actor_id: "admin-1",
      target_type: "user",
      limit: 10,
      offset: 0,
    });
    expect(apiRequest).toHaveBeenCalledWith("/admin/audit", {
      query: {
        action: "user.update",
        actor_id: "admin-1",
        target_type: "user",
        limit: 10,
        offset: 0,
      },
    });
    expect(result).toEqual([sampleAuditLog]);
  });

  it("triggerStorageCleanup sends POST", async () => {
    apiRequest.mockResolvedValueOnce(null);
    const result = await triggerStorageCleanup();
    expect(apiRequest).toHaveBeenCalledWith("/admin/storage/cleanup", {
      method: "POST",
    });
    expect(result).toBeNull();
  });

  it("syncTags sends POST to /admin/sync-tags", async () => {
    const body = { track_id: "track-1", dry_run: false };
    apiRequest.mockResolvedValueOnce({ enqueued: 1, status: "queued" });
    const result = await syncTags(body);
    expect(apiRequest).toHaveBeenCalledWith("/admin/sync-tags", {
      method: "POST",
      body,
    });
    expect(result).toEqual({ enqueued: 1, status: "queued" });
  });

  it("rehashAudio sends POST to /admin/rehash-audio", async () => {
    const body = { dry_run: true };
    apiRequest.mockResolvedValueOnce({ task_id: "task-1", status: "queued" });
    const result = await rehashAudio(body);
    expect(apiRequest).toHaveBeenCalledWith("/admin/rehash-audio", {
      method: "POST",
      body,
    });
    expect(result).toEqual({ task_id: "task-1", status: "queued" });
  });

  it("provisionFederationKeys sends POST to /admin/provision-federation-keys", async () => {
    const body = { dry_run: false };
    apiRequest.mockResolvedValueOnce({ task_id: "task-2", status: "queued" });
    const result = await provisionFederationKeys(body);
    expect(apiRequest).toHaveBeenCalledWith(
      "/admin/provision-federation-keys",
      {
        method: "POST",
        body,
      },
    );
    expect(result).toEqual({ task_id: "task-2", status: "queued" });
  });
});
