import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listUserProviders,
  listUserExternalLibraries,
  createUserExternalLibrary,
  getUserExternalLibrary,
  updateUserExternalLibrary,
  deleteUserExternalLibrary,
  syncUserExternalLibrary,
  listUserSyncRuns,
  listUserExternalTracks,
  restoreUserExternalTrack,
  deleteUserExternalTrack,
  adminListExternalLibraries,
  adminGetExternalLibrary,
  adminCreateExternalLibrary,
  adminUpdateExternalLibrary,
  adminDeleteExternalLibrary,
  adminListExternalTracks,
  adminBulkDeleteExternalTracks,
  resolveUploadDuplicate,
  type ExternalLibraryResponse,
  type ExternalLibraryCreate,
  type ExternalLibraryUpdate,
  type ExternalTrackDeleteRequest,
} from "./externalLibraries";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
  apiRequestWithHeaders: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);
const apiRequestWithHeaders = vi.mocked(client.apiRequestWithHeaders);

const sampleLibrary: ExternalLibraryResponse = {
  id: "el1",
  library_id: "lib1",
  provider_type: "s3",
  scope: "user",
  name: "Test External Library",
  config: { bucket: "music" },
  enabled: true,
  include_in_library_index: false,
  sync_enabled: true,
  sync_interval_seconds: null,
  can_manage: true,
  can_sync: true,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

function mockListHeaders() {
  return new Headers({ "X-Total-Count": "1" });
}

describe("externalLibraries api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequestWithHeaders.mockReset();
  });

  it("listUserProviders fetches providers", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listUserProviders();
    expect(apiRequest).toHaveBeenCalledWith("/external-libraries/providers");
  });

  it("listUserExternalLibraries returns libraries with total", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleLibrary],
      headers: mockListHeaders(),
    });
    const result = await listUserExternalLibraries({ limit: 10, offset: 0 });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/external-libraries/", {
      query: { limit: 10, offset: 0 },
    });
    expect(result.libraries).toEqual([sampleLibrary]);
    expect(result.total).toBe(1);
  });

  it("createUserExternalLibrary posts a new library", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const body: ExternalLibraryCreate = {
      provider_type: "s3",
      name: "New",
      config: { bucket: "music" },
      visibility: "private",
      enabled: true,
      sync_enabled: true,
      include_in_library_index: false,
    };
    await createUserExternalLibrary(body);
    expect(apiRequest).toHaveBeenCalledWith("/external-libraries/", {
      method: "POST",
      body,
    });
  });

  it("getUserExternalLibrary fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const result = await getUserExternalLibrary("el1");
    expect(apiRequest).toHaveBeenCalledWith("/external-libraries/el1");
    expect(result).toEqual(sampleLibrary);
  });

  it("updateUserExternalLibrary patches with body", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const body: ExternalLibraryUpdate = { name: "Updated" };
    await updateUserExternalLibrary("el1", body);
    expect(apiRequest).toHaveBeenCalledWith("/external-libraries/el1", {
      method: "PATCH",
      body,
    });
  });

  it("deleteUserExternalLibrary sends DELETE", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteUserExternalLibrary("el1");
    expect(apiRequest).toHaveBeenCalledWith("/external-libraries/el1", {
      method: "DELETE",
    });
  });

  it("syncUserExternalLibrary posts sync", async () => {
    apiRequest.mockResolvedValueOnce({ sync_run_id: "sr1" });
    const result = await syncUserExternalLibrary("el1");
    expect(apiRequest).toHaveBeenCalledWith("/external-libraries/el1/sync", {
      method: "POST",
      body: { include_tombstones: false },
    });
    expect(result).toEqual({ sync_run_id: "sr1" });
  });

  it("listUserSyncRuns fetches sync runs", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [],
      headers: mockListHeaders(),
    });
    await listUserSyncRuns("el1");
    expect(apiRequestWithHeaders).toHaveBeenCalledWith(
      "/external-libraries/el1/sync-runs",
      { query: undefined },
    );
  });

  it("listUserExternalTracks fetches tracks", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [],
      headers: mockListHeaders(),
    });
    await listUserExternalTracks("el1", { state: "active" });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith(
      "/external-libraries/el1/tracks",
      { query: { state: "active" } },
    );
  });

  it("restoreUserExternalTrack posts restore", async () => {
    apiRequest.mockResolvedValueOnce({});
    await restoreUserExternalTrack("el1", "et1");
    expect(apiRequest).toHaveBeenCalledWith(
      "/external-libraries/el1/tracks/et1/restore",
      { method: "POST" },
    );
  });

  it("deleteUserExternalTrack sends DELETE with body", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    const body: ExternalTrackDeleteRequest = {
      delete_source: true,
      remove_songhive_track: true,
    };
    await deleteUserExternalTrack("el1", "et1", body);
    expect(apiRequest).toHaveBeenCalledWith(
      "/external-libraries/el1/tracks/et1",
      { method: "DELETE", body },
    );
  });

  it("adminListExternalLibraries fetches admin libraries", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleLibrary],
      headers: mockListHeaders(),
    });
    const result = await adminListExternalLibraries({ include_user: true });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith(
      "/admin/external-libraries/",
      { query: { include_user: true } },
    );
    expect(result.total).toBe(1);
  });

  it("adminGetExternalLibrary fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    await adminGetExternalLibrary("el1");
    expect(apiRequest).toHaveBeenCalledWith("/admin/external-libraries/el1");
  });

  it("adminCreateExternalLibrary posts to admin endpoint", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    const body: ExternalLibraryCreate = {
      provider_type: "s3",
      name: "Admin",
      config: {},
      visibility: "public",
      enabled: true,
      sync_enabled: true,
      include_in_library_index: true,
    };
    await adminCreateExternalLibrary(body);
    expect(apiRequest).toHaveBeenCalledWith("/admin/external-libraries/", {
      method: "POST",
      body,
    });
  });

  it("adminUpdateExternalLibrary patches", async () => {
    apiRequest.mockResolvedValueOnce(sampleLibrary);
    await adminUpdateExternalLibrary("el1", { enabled: false });
    expect(apiRequest).toHaveBeenCalledWith("/admin/external-libraries/el1", {
      method: "PATCH",
      body: { enabled: false },
    });
  });

  it("adminDeleteExternalLibrary sends DELETE", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await adminDeleteExternalLibrary("el1");
    expect(apiRequest).toHaveBeenCalledWith("/admin/external-libraries/el1", {
      method: "DELETE",
    });
  });

  it("adminListExternalTracks fetches admin tracks", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [],
      headers: mockListHeaders(),
    });
    await adminListExternalTracks("el1");
    expect(apiRequestWithHeaders).toHaveBeenCalledWith(
      "/admin/external-libraries/el1/tracks",
      { query: undefined },
    );
  });

  it("adminBulkDeleteExternalTracks posts bulk delete", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await adminBulkDeleteExternalTracks("el1", {
      external_track_ids: ["et1", "et2"],
      delete_source: false,
      remove_songhive_track: false,
    });
    expect(apiRequest).toHaveBeenCalledWith(
      "/admin/external-libraries/el1/tracks/bulk-delete",
      {
        method: "POST",
        body: {
          external_track_ids: ["et1", "et2"],
          delete_source: false,
          remove_songhive_track: false,
        },
      },
    );
  });

  it("resolveUploadDuplicate posts a resolution", async () => {
    apiRequest.mockResolvedValueOnce({});
    await resolveUploadDuplicate("token", "keep_local");
    expect(apiRequest).toHaveBeenCalledWith("/files/upload/resolve-duplicate", {
      method: "POST",
      body: { token: "token", action: "keep_local" },
    });
  });
});
