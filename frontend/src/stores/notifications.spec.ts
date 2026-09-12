import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import * as notificationsApi from "@/api/notifications";
import type { NotificationResponse } from "@/api/notifications";
import { eventBus } from "@/api/ws";
import { useNotificationsStore } from "./notifications";
import { useToastStore } from "./toast";

type WsHandler = (event: { type: string; data: unknown }) => void;

vi.mock("@/api/notifications", () => ({
  listNotifications: vi.fn(),
  getUnreadCount: vi.fn(),
  markSeen: vi.fn(),
  markUnseen: vi.fn(),
  markAllSeen: vi.fn(),
  deleteNotification: vi.fn(),
  deleteNotifications: vi.fn(),
  clearNotifications: vi.fn(),
  getNotificationPreferences: vi.fn(),
  updateNotificationPreferences: vi.fn(),
}));

vi.mock("@/api/ws", () => {
  const handlers = new Map<string, Set<WsHandler>>();
  const bus = {
    on: vi.fn((type: string, handler: WsHandler) => {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type)!.add(handler);
    }),
    off: vi.fn((type: string, handler: WsHandler) => {
      handlers.get(type)?.delete(handler);
    }),
    connect: vi.fn(),
    disconnect: vi.fn(),
    emit(type: string, data: unknown) {
      handlers.get(type)?.forEach((handler) => handler({ type, data }));
    },
  };
  return { eventBus: bus };
});

const emit = (type: string, data: unknown) =>
  (eventBus as unknown as { emit: (t: string, d: unknown) => void }).emit(
    type,
    data,
  );

const listNotifications = vi.mocked(notificationsApi.listNotifications);
const getUnreadCount = vi.mocked(notificationsApi.getUnreadCount);
const markSeenApi = vi.mocked(notificationsApi.markSeen);
const markUnseenApi = vi.mocked(notificationsApi.markUnseen);
const markAllSeenApi = vi.mocked(notificationsApi.markAllSeen);
const deleteNotificationApi = vi.mocked(notificationsApi.deleteNotification);
const deleteNotificationsApi = vi.mocked(notificationsApi.deleteNotifications);
const clearNotificationsApi = vi.mocked(notificationsApi.clearNotifications);

function createNotification(
  id: string,
  overrides: Partial<NotificationResponse> = {},
): NotificationResponse {
  return {
    id,
    type: "like",
    actor_url: "https://example.com/users/alice",
    source_url: "https://example.com/objects/1",
    payload: {},
    seen_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("useNotificationsStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("connect registers the handler, opens the bus and fetches the count", async () => {
    getUnreadCount.mockResolvedValueOnce(3);
    const store = useNotificationsStore();
    store.connect();
    expect(eventBus.on).toHaveBeenCalledWith(
      "notification",
      expect.any(Function),
    );
    expect(eventBus.connect).toHaveBeenCalled();
    await vi.waitFor(() => expect(store.unreadCount).toBe(3));
  });

  it("disconnect closes the bus", () => {
    const store = useNotificationsStore();
    store.disconnect();
    expect(eventBus.disconnect).toHaveBeenCalled();
  });

  it("load fetches the first page", async () => {
    const notification = createNotification("n1");
    listNotifications.mockResolvedValueOnce({
      items: [notification],
      total: 5,
    });
    const store = useNotificationsStore();
    await store.load();
    expect(listNotifications).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
      seen: undefined,
    });
    expect(store.items).toEqual([notification]);
    expect(store.total).toBe(5);
  });

  it("unread filter maps to seen=false", async () => {
    listNotifications.mockResolvedValue({ items: [], total: 0 });
    const store = useNotificationsStore();
    await store.setFilter("unread");
    expect(listNotifications).toHaveBeenCalledWith({
      limit: 20,
      offset: 0,
      seen: false,
    });
  });

  it("toggleType narrows the list to the selected types", async () => {
    listNotifications.mockResolvedValue({ items: [], total: 0 });
    const store = useNotificationsStore();
    await store.toggleType("follow");
    expect(listNotifications).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      seen: undefined,
      type: "follow",
    });

    await store.toggleType("mention");
    expect(listNotifications).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      seen: undefined,
      type: "follow,mention",
    });

    // Deselecting every type returns to the unfiltered list.
    await store.toggleType("follow");
    await store.toggleType("mention");
    expect(store.typeFilter.size).toBe(0);
    expect(listNotifications).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 0,
      seen: undefined,
      type: undefined,
    });
  });

  it("ws events of filtered-out types bump the badge but not the list", async () => {
    listNotifications.mockResolvedValue({
      items: [createNotification("n1", { type: "follow" })],
      total: 1,
    });
    getUnreadCount.mockResolvedValueOnce(1);
    const store = useNotificationsStore();
    store.connect();
    await vi.waitFor(() => expect(store.unreadCount).toBe(1));
    await store.toggleType("follow");

    emit("notification", createNotification("n2", { type: "like" }));
    expect(store.items.map((i) => i.id)).toEqual(["n1"]);
    expect(store.total).toBe(1);
    expect(store.unreadCount).toBe(2);

    emit("notification", createNotification("n3", { type: "follow" }));
    expect(store.items.map((i) => i.id)).toEqual(["n3", "n1"]);
    expect(store.total).toBe(2);
    expect(store.unreadCount).toBe(3);
  });

  it("loadMore appends the next page", async () => {
    listNotifications
      .mockResolvedValueOnce({ items: [createNotification("n1")], total: 2 })
      .mockResolvedValueOnce({ items: [createNotification("n2")], total: 2 });
    const store = useNotificationsStore();
    await store.load();
    await store.loadMore();
    expect(listNotifications).toHaveBeenLastCalledWith({
      limit: 20,
      offset: 1,
      seen: undefined,
    });
    expect(store.items.map((i) => i.id)).toEqual(["n1", "n2"]);
    expect(store.hasMore).toBe(false);
  });

  it("markSeen updates items and unread count optimistically", async () => {
    const notification = createNotification("n1");
    listNotifications.mockResolvedValueOnce({
      items: [notification],
      total: 1,
    });
    getUnreadCount.mockResolvedValueOnce(1);
    markSeenApi.mockResolvedValueOnce(undefined);
    const store = useNotificationsStore();
    store.connect();
    await vi.waitFor(() => expect(store.unreadCount).toBe(1));
    await store.load();

    const pending = markSeenApi.mock.calls.length;
    await store.markSeen(["n1"]);
    expect(markSeenApi).toHaveBeenCalledTimes(pending + 1);
    expect(store.items[0].seen_at).not.toBeNull();
    expect(store.unreadCount).toBe(0);
  });

  it("markSeen rolls back and toasts on failure", async () => {
    const notification = createNotification("n1");
    listNotifications.mockResolvedValueOnce({
      items: [notification],
      total: 1,
    });
    markSeenApi.mockRejectedValueOnce(new Error("boom"));
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.unreadCount = 1;
    await store.load();

    await store.markSeen(["n1"]);
    expect(store.items[0].seen_at).toBeNull();
    expect(store.unreadCount).toBe(1);
    expect(toast.toasts.some((t) => t.type === "error")).toBe(true);
  });

  it("markUnseen restores unseen state and increments the count", async () => {
    const notification = createNotification("n1", {
      seen_at: "2026-01-02T00:00:00Z",
    });
    listNotifications.mockResolvedValueOnce({
      items: [notification],
      total: 1,
    });
    markUnseenApi.mockResolvedValueOnce(undefined);
    const store = useNotificationsStore();
    store.unreadCount = 0;
    await store.load();

    await store.markUnseen(["n1"]);
    expect(store.items[0].seen_at).toBeNull();
    expect(store.unreadCount).toBe(1);
  });

  it("markSeen under the unread filter drops the item after success", async () => {
    const notification = createNotification("n1");
    listNotifications.mockResolvedValue({
      items: [notification],
      total: 1,
    });
    markSeenApi.mockResolvedValueOnce(undefined);
    const store = useNotificationsStore();
    store.unreadCount = 1;
    await store.setFilter("unread");

    await store.markSeen(["n1"]);
    expect(store.items).toEqual([]);
    expect(store.total).toBe(0);
    expect(store.unreadCount).toBe(0);
  });

  it("markAllSeen clears the unread count", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    markAllSeenApi.mockResolvedValueOnce(undefined);
    const store = useNotificationsStore();
    store.unreadCount = 2;
    await store.load();
    await store.markAllSeen();
    expect(markAllSeenApi).toHaveBeenCalled();
    expect(store.unreadCount).toBe(0);
    expect(store.items.every((i) => i.seen_at != null)).toBe(true);
  });

  it("ws events increment the count and prepend when loaded", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    getUnreadCount.mockResolvedValueOnce(0);
    const store = useNotificationsStore();
    store.connect();
    await store.load();

    emit("notification", createNotification("n2"));
    expect(store.unreadCount).toBe(1);
    expect(store.total).toBe(2);
    expect(store.items.map((i) => i.id)).toEqual(["n2", "n1"]);
  });

  it("ws events do not prepend before the list is loaded", async () => {
    getUnreadCount.mockResolvedValueOnce(0);
    const store = useNotificationsStore();
    store.connect();
    await vi.waitFor(() => expect(getUnreadCount).toHaveBeenCalled());

    emit("notification", createNotification("n1"));
    expect(store.unreadCount).toBe(1);
    expect(store.items).toEqual([]);
  });

  it("ws events push an info toast naming the actor and action", async () => {
    getUnreadCount.mockResolvedValueOnce(0);
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.connect();
    await vi.waitFor(() => expect(getUnreadCount).toHaveBeenCalled());

    emit(
      "notification",
      createNotification("n1", {
        payload: { actor_name: "Alice" },
      }),
    );

    const last = toast.toasts[toast.toasts.length - 1];
    expect(last.type).toBe("info");
    expect(last.message).toBe("Alice liked your post");
  });

  it("ws toast falls back for unknown types and missing actor names", async () => {
    getUnreadCount.mockResolvedValueOnce(0);
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.connect();
    await vi.waitFor(() => expect(getUnreadCount).toHaveBeenCalled());

    emit("notification", createNotification("n1", { type: "bogus" }));

    const last = toast.toasts[toast.toasts.length - 1];
    expect(last.type).toBe("info");
    expect(last.message).toBe("Someone sent you a notification");
  });

  it("ws events with non-notification data do not toast", async () => {
    getUnreadCount.mockResolvedValueOnce(0);
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.connect();
    await vi.waitFor(() => expect(getUnreadCount).toHaveBeenCalled());

    emit("notification", { nope: true });
    expect(toast.toasts).toEqual([]);
  });

  it("remove deletes a single notification and updates counts", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    deleteNotificationApi.mockResolvedValueOnce(undefined);
    const store = useNotificationsStore();
    store.unreadCount = 2;
    await store.load();

    await store.remove(["n1"]);
    expect(deleteNotificationApi).toHaveBeenCalledWith("n1");
    expect(deleteNotificationsApi).not.toHaveBeenCalled();
    expect(store.items.map((i) => i.id)).toEqual(["n2"]);
    expect(store.total).toBe(1);
    expect(store.unreadCount).toBe(1);
  });

  it("remove uses the bulk endpoint for multiple ids", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1"),
        createNotification("n2"),
        createNotification("n3"),
      ],
      total: 3,
    });
    deleteNotificationsApi.mockResolvedValueOnce(2);
    const store = useNotificationsStore();
    store.unreadCount = 3;
    await store.load();

    await store.remove(["n1", "n3"]);
    expect(deleteNotificationsApi).toHaveBeenCalledWith(["n1", "n3"]);
    expect(store.items.map((i) => i.id)).toEqual(["n2"]);
    expect(store.total).toBe(1);
    expect(store.unreadCount).toBe(1);
  });

  it("remove does not decrement unread for already-seen items", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", { seen_at: "2026-01-02T00:00:00Z" }),
        createNotification("n2"),
      ],
      total: 2,
    });
    deleteNotificationsApi.mockResolvedValueOnce(2);
    const store = useNotificationsStore();
    store.unreadCount = 1;
    await store.load();

    await store.remove(["n1", "n2"]);
    expect(store.unreadCount).toBe(0);
    expect(store.total).toBe(0);
  });

  it("remove rolls back and toasts on failure", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    deleteNotificationApi.mockRejectedValueOnce(new Error("boom"));
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.unreadCount = 1;
    await store.load();

    await store.remove(["n1"]);
    expect(store.items.map((i) => i.id)).toEqual(["n1"]);
    expect(store.total).toBe(1);
    expect(store.unreadCount).toBe(1);
    expect(toast.toasts.some((t) => t.type === "error")).toBe(true);
  });

  it("clearAll empties the list and resets the unread count", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1"), createNotification("n2")],
      total: 2,
    });
    clearNotificationsApi.mockResolvedValueOnce(2);
    const store = useNotificationsStore();
    store.unreadCount = 2;
    await store.load();

    await store.clearAll();
    expect(clearNotificationsApi).toHaveBeenCalled();
    expect(store.items).toEqual([]);
    expect(store.total).toBe(0);
    expect(store.unreadCount).toBe(0);
  });

  it("clearAll rolls back and toasts on failure", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    clearNotificationsApi.mockRejectedValueOnce(new Error("boom"));
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.unreadCount = 1;
    await store.load();

    await store.clearAll();
    expect(store.items.map((i) => i.id)).toEqual(["n1"]);
    expect(store.unreadCount).toBe(1);
    expect(toast.toasts.some((t) => t.type === "error")).toBe(true);
  });

  it("notification_deleted ws events drop the rows", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1"),
        createNotification("n2", { seen_at: "2026-01-02T00:00:00Z" }),
      ],
      total: 2,
    });
    getUnreadCount.mockResolvedValueOnce(1);
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.connect();
    await vi.waitFor(() => expect(store.unreadCount).toBe(1));
    await store.load();

    emit("notification_deleted", { ids: ["n1", "n2"] });
    expect(store.items).toEqual([]);
    expect(store.total).toBe(0);
    expect(store.unreadCount).toBe(0);
    // Retraction is silent — no toast.
    expect(toast.toasts).toEqual([]);
  });

  it("notification_deleted ws events with malformed data are ignored", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    const store = useNotificationsStore();
    store.connect();
    await store.load();

    emit("notification_deleted", { nope: true });
    emit("notification_deleted", null);
    expect(store.items.map((i) => i.id)).toEqual(["n1"]);
  });

  it("notification_updated ws events replace rows in place", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [
        createNotification("n1", { payload: { object_content: "old" } }),
        createNotification("n2", { seen_at: "2026-01-02T00:00:00Z" }),
      ],
      total: 2,
    });
    getUnreadCount.mockResolvedValueOnce(1);
    const store = useNotificationsStore();
    const toast = useToastStore();
    store.connect();
    await vi.waitFor(() => expect(store.unreadCount).toBe(1));
    await store.load();

    const edited = createNotification("n1", {
      payload: { object_content: "edited" },
    });
    emit("notification_updated", {
      notifications: [edited, createNotification("n-new")],
    });

    // The matching row is replaced in place; unknown ids are ignored and
    // no row is reordered, re-counted, or toasted.
    expect(store.items.map((i) => i.id)).toEqual(["n1", "n2"]);
    expect(store.items[0].payload?.object_content).toBe("edited");
    expect(store.items[1].seen_at).toBe("2026-01-02T00:00:00Z");
    expect(store.total).toBe(2);
    expect(store.unreadCount).toBe(1);
    expect(toast.toasts).toEqual([]);
  });

  it("notification_updated ws events with malformed data are ignored", async () => {
    listNotifications.mockResolvedValueOnce({
      items: [createNotification("n1")],
      total: 1,
    });
    const store = useNotificationsStore();
    store.connect();
    await store.load();

    emit("notification_updated", { nope: true });
    emit("notification_updated", { notifications: [{ nope: true }] });
    emit("notification_updated", null);
    expect(store.items.map((i) => i.id)).toEqual(["n1"]);
    expect(store.items[0].payload).toEqual({});
  });
});
