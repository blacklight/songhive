import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listNotifications,
  getUnreadCount,
  markSeen,
  markUnseen,
  markAllSeen,
  deleteNotification,
  deleteNotifications,
  clearNotifications,
  getNotificationPreferences,
  updateNotificationPreferences,
  type NotificationResponse,
  type NotificationPreferenceItem,
} from "./notifications";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
  apiRequestWithHeaders: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);
const apiRequestWithHeaders = vi.mocked(client.apiRequestWithHeaders);

const sampleNotification: NotificationResponse = {
  id: "n1",
  type: "like",
  actor_url: "https://example.com/users/alice",
  source_url: "https://example.com/objects/1",
  payload: { actor_name: "alice" },
  seen_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

function headersWithTotal(total: number): Headers {
  return new Headers({ "X-Total-Count": String(total) });
}

describe("notifications api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequestWithHeaders.mockReset();
  });

  it("listNotifications fetches the endpoint and reads X-Total-Count", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleNotification],
      headers: headersWithTotal(42),
    });
    const result = await listNotifications({ limit: 10, offset: 5 });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/notifications/", {
      query: { limit: 10, offset: 5, seen: undefined },
    });
    expect(result).toEqual({ items: [sampleNotification], total: 42 });
  });

  it("listNotifications passes the seen filter", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [],
      headers: headersWithTotal(0),
    });
    await listNotifications({ seen: false });
    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/notifications/", {
      query: { limit: undefined, offset: undefined, seen: false },
    });
  });

  it("listNotifications falls back to body length without the header", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleNotification],
      headers: new Headers(),
    });
    const result = await listNotifications();
    expect(result.total).toBe(1);
  });

  it("getUnreadCount returns the count", async () => {
    apiRequest.mockResolvedValueOnce({ count: 7 });
    await expect(getUnreadCount()).resolves.toBe(7);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/unread-count");
  });

  it("markSeen posts the id list", async () => {
    apiRequest.mockResolvedValueOnce({ updated: 2 });
    await markSeen(["a", "b"]);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/seen", {
      method: "POST",
      body: { ids: ["a", "b"] },
    });
  });

  it("markUnseen posts the id list", async () => {
    apiRequest.mockResolvedValueOnce({ updated: 1 });
    await markUnseen(["a"]);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/unseen", {
      method: "POST",
      body: { ids: ["a"] },
    });
  });

  it("markAllSeen posts without a body", async () => {
    apiRequest.mockResolvedValueOnce({ updated: 3 });
    await markAllSeen();
    expect(apiRequest).toHaveBeenCalledWith("/notifications/seen-all", {
      method: "POST",
    });
  });

  it("deleteNotification issues a DELETE for the id", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteNotification("n1");
    expect(apiRequest).toHaveBeenCalledWith("/notifications/n1", {
      method: "DELETE",
    });
  });

  it("deleteNotifications posts the id list and returns the count", async () => {
    apiRequest.mockResolvedValueOnce({ deleted: 2 });
    await expect(deleteNotifications(["a", "b"])).resolves.toBe(2);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/delete", {
      method: "POST",
      body: { ids: ["a", "b"] },
    });
  });

  it("clearNotifications posts and returns the count", async () => {
    apiRequest.mockResolvedValueOnce({ deleted: 5 });
    await expect(clearNotifications()).resolves.toBe(5);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/clear", {
      method: "POST",
    });
  });

  it("getNotificationPreferences returns the preference list", async () => {
    const prefs: NotificationPreferenceItem[] = [
      { type: "follow", in_app: true, email: false, email_digest: false },
    ];
    apiRequest.mockResolvedValueOnce({ preferences: prefs });
    await expect(getNotificationPreferences()).resolves.toEqual(prefs);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/preferences");
  });

  it("updateNotificationPreferences PUTs the preference list", async () => {
    const prefs: NotificationPreferenceItem[] = [
      { type: "like", in_app: true, email: true, email_digest: false },
    ];
    apiRequest.mockResolvedValueOnce({ preferences: prefs });
    await expect(updateNotificationPreferences(prefs)).resolves.toEqual(prefs);
    expect(apiRequest).toHaveBeenCalledWith("/notifications/preferences", {
      method: "PUT",
      body: { preferences: prefs },
    });
  });
});
