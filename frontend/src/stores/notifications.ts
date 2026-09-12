import { computed, ref, type Ref } from "vue";
import { defineStore } from "pinia";
import * as notificationsApi from "@/api/notifications";
import type {
  NotificationResponse,
  NotificationType,
} from "@/api/notifications";
import { getApiErrorMessage } from "@/api/client";
import { eventBus, type WsEvent } from "@/api/ws";
import { useToastStore } from "@/stores/toast";
import { i18n } from "@/i18n";

export type NotificationFilter = "all" | "unread";

const PAGE_SIZE = 20;
const WS_EVENT = "notification";
const WS_DELETED_EVENT = "notification_deleted";
const WS_UPDATED_EVENT = "notification_updated";

function isNotification(value: unknown): value is NotificationResponse {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as NotificationResponse).id === "string"
  );
}

function isDeletedPayload(value: unknown): value is { ids: string[] } {
  return (
    !!value &&
    typeof value === "object" &&
    Array.isArray((value as { ids?: unknown }).ids)
  );
}

function isUpdatedPayload(
  value: unknown,
): value is { notifications: unknown[] } {
  return (
    !!value &&
    typeof value === "object" &&
    Array.isArray((value as { notifications?: unknown }).notifications)
  );
}

export const useNotificationsStore = defineStore("notifications", () => {
  const toast = useToastStore();

  const unreadCount = ref(0);
  const items: Ref<NotificationResponse[]> = ref([]);
  const total = ref(0);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error: Ref<string | null> = ref(null);
  const filter: Ref<NotificationFilter> = ref("all");
  // Active notification types; an empty set means "all types" (no param sent).
  const typeFilter: Ref<Set<NotificationType>> = ref(new Set());

  const loaded = ref(false);
  const hasMore = computed(() => items.value.length < total.value);
  const seenParam = computed(() =>
    filter.value === "unread" ? false : undefined,
  );
  const typeParam = computed(() =>
    typeFilter.value.size === 0 ? undefined : [...typeFilter.value].join(","),
  );

  let handlerRegistered = false;

  function errorMessage(err: unknown): string {
    return (
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : i18n.global.t("errors.unknown"))
    );
  }

  async function fetchUnreadCount(): Promise<void> {
    try {
      unreadCount.value = await notificationsApi.getUnreadCount();
    } catch {
      // The badge is best-effort; a failed count fetch must not break the app.
    }
  }

  async function load(): Promise<void> {
    if (loading.value) return;
    loading.value = true;
    error.value = null;
    try {
      const page = await notificationsApi.listNotifications({
        limit: PAGE_SIZE,
        offset: 0,
        seen: seenParam.value,
        type: typeParam.value,
      });
      items.value = page.items;
      total.value = page.total;
      loaded.value = true;
    } catch (err) {
      error.value = errorMessage(err);
      items.value = [];
      total.value = 0;
    } finally {
      loading.value = false;
    }
  }

  async function loadMore(): Promise<void> {
    if (loadingMore.value || loading.value || !hasMore.value) return;
    loadingMore.value = true;
    error.value = null;
    try {
      const page = await notificationsApi.listNotifications({
        limit: PAGE_SIZE,
        offset: items.value.length,
        seen: seenParam.value,
        type: typeParam.value,
      });
      const known = new Set(items.value.map((item) => item.id));
      items.value = [
        ...items.value,
        ...page.items.filter((item) => !known.has(item.id)),
      ];
      total.value = page.total;
    } catch (err) {
      error.value = errorMessage(err);
    } finally {
      loadingMore.value = false;
    }
  }

  async function setFilter(next: NotificationFilter): Promise<void> {
    if (next === filter.value) return;
    filter.value = next;
    await load();
  }

  function matchesTypeFilter(type: string): boolean {
    return (
      typeFilter.value.size === 0 ||
      typeFilter.value.has(type as NotificationType)
    );
  }

  async function toggleType(type: NotificationType): Promise<void> {
    if (typeFilter.value.has(type)) {
      typeFilter.value.delete(type);
    } else {
      typeFilter.value.add(type);
    }
    await load();
  }

  function applyLocalSeen(ids: string[], seen: boolean): number {
    let delta = 0;
    const timestamp = seen ? new Date().toISOString() : null;
    for (const item of items.value) {
      if (!ids.includes(item.id)) continue;
      const wasSeen = item.seen_at != null;
      if (wasSeen === seen) continue;
      item.seen_at = timestamp;
      delta += 1;
    }
    return delta;
  }

  function dropFiltered(ids: string[], seen: boolean): void {
    // Under the "unread" filter, items marked seen no longer match and are
    // removed locally so they do not linger until the next reload.
    if (filter.value !== "unread" || !seen) return;
    const removed = new Set(ids);
    const before = items.value.length;
    items.value = items.value.filter((item) => !removed.has(item.id));
    total.value = Math.max(0, total.value - (before - items.value.length));
  }

  async function setSeen(ids: string[], seen: boolean): Promise<void> {
    if (ids.length === 0) return;
    const snapshots = new Map(
      items.value
        .filter((item) => ids.includes(item.id))
        .map((item) => [item.id, item.seen_at ?? null]),
    );
    const delta = applyLocalSeen(ids, seen);
    unreadCount.value = Math.max(
      0,
      unreadCount.value + (seen ? -delta : delta),
    );
    try {
      if (seen) await notificationsApi.markSeen(ids);
      else await notificationsApi.markUnseen(ids);
      dropFiltered(ids, seen);
    } catch (err) {
      for (const item of items.value) {
        const snapshot = snapshots.get(item.id);
        if (snapshot !== undefined) item.seen_at = snapshot;
      }
      unreadCount.value = Math.max(
        0,
        unreadCount.value - (seen ? -delta : delta),
      );
      toast.push({
        type: "error",
        message: i18n.global.t("notifications.errors.updateFailed", {
          message: errorMessage(err),
        }),
      });
    }
  }

  async function markSeen(ids: string[]): Promise<void> {
    await setSeen(ids, true);
  }

  async function markUnseen(ids: string[]): Promise<void> {
    await setSeen(ids, false);
  }

  function applyLocalDelete(ids: string[]): {
    items: NotificationResponse[];
    total: number;
    unread: number;
  } {
    const removed = new Set(ids);
    const snapshot = {
      items: items.value,
      total: total.value,
      unread: unreadCount.value,
    };
    let unseenRemoved = 0;
    items.value = items.value.filter((item) => {
      if (!removed.has(item.id)) return true;
      if (item.seen_at == null) unseenRemoved += 1;
      return false;
    });
    total.value = Math.max(
      0,
      total.value - (snapshot.items.length - items.value.length),
    );
    unreadCount.value = Math.max(0, unreadCount.value - unseenRemoved);
    return snapshot;
  }

  async function remove(ids: string[]): Promise<void> {
    if (ids.length === 0) return;
    const snapshot = applyLocalDelete(ids);
    try {
      if (ids.length === 1) {
        await notificationsApi.deleteNotification(ids[0]);
      } else {
        await notificationsApi.deleteNotifications(ids);
      }
    } catch (err) {
      items.value = snapshot.items;
      total.value = snapshot.total;
      unreadCount.value = snapshot.unread;
      toast.push({
        type: "error",
        message: i18n.global.t("notifications.errors.deleteFailed", {
          message: errorMessage(err),
        }),
      });
    }
  }

  async function clearAll(): Promise<void> {
    const snapshot = {
      items: items.value,
      total: total.value,
      unread: unreadCount.value,
    };
    items.value = [];
    total.value = 0;
    unreadCount.value = 0;
    try {
      await notificationsApi.clearNotifications();
    } catch (err) {
      items.value = snapshot.items;
      total.value = snapshot.total;
      unreadCount.value = snapshot.unread;
      toast.push({
        type: "error",
        message: i18n.global.t("notifications.errors.deleteFailed", {
          message: errorMessage(err),
        }),
      });
    }
  }

  async function markAllSeen(): Promise<void> {
    const snapshots = new Map(
      items.value.map((item) => [item.id, item.seen_at ?? null]),
    );
    const previousUnread = unreadCount.value;
    const timestamp = new Date().toISOString();
    for (const item of items.value) {
      if (item.seen_at == null) item.seen_at = timestamp;
    }
    unreadCount.value = 0;
    try {
      await notificationsApi.markAllSeen();
      if (filter.value === "unread") {
        total.value = Math.max(0, total.value - items.value.length);
        items.value = [];
      }
    } catch (err) {
      for (const item of items.value) {
        const snapshot = snapshots.get(item.id);
        if (snapshot !== undefined) item.seen_at = snapshot;
      }
      unreadCount.value = previousUnread;
      toast.push({
        type: "error",
        message: i18n.global.t("notifications.errors.updateFailed", {
          message: errorMessage(err),
        }),
      });
    }
  }

  function toastMessage(notification: NotificationResponse): string {
    const name = notification.payload?.actor_name;
    const actor =
      typeof name === "string" && name
        ? name
        : i18n.global.t("notifications.someone");
    const key = `notifications.types.${notification.type}`;
    const translated = i18n.global.t(key);
    const action =
      translated === key
        ? i18n.global.t("notifications.types.unknown")
        : translated;
    return i18n.global.t("notifications.toast", { actor, action });
  }

  function onWsEvent(event: WsEvent): void {
    if (!isNotification(event.data)) return;
    const notification = event.data;
    unreadCount.value += 1;
    // ``total``/``items`` reflect the active filters; an event of a
    // filtered-out type still bumps the badge but stays off the list.
    if (matchesTypeFilter(notification.type)) {
      total.value += 1;
      if (
        loaded.value &&
        !items.value.some((item) => item.id === notification.id)
      ) {
        items.value.unshift(notification);
      }
    }
    toast.push({ type: "info", message: toastMessage(notification) });
  }

  function onWsDeletedEvent(event: WsEvent): void {
    if (!isDeletedPayload(event.data)) return;
    applyLocalDelete(event.data.ids);
  }

  function onWsUpdatedEvent(event: WsEvent): void {
    if (!isUpdatedPayload(event.data)) return;
    const byId = new Map<string, NotificationResponse>();
    for (const entry of event.data.notifications) {
      if (isNotification(entry)) byId.set(entry.id, entry);
    }
    if (byId.size === 0) return;
    // Edited notifications keep their position; only their contents change.
    items.value = items.value.map((item) => byId.get(item.id) ?? item);
  }

  function connect(): void {
    if (!handlerRegistered) {
      eventBus.on(WS_EVENT, onWsEvent);
      eventBus.on(WS_DELETED_EVENT, onWsDeletedEvent);
      eventBus.on(WS_UPDATED_EVENT, onWsUpdatedEvent);
      handlerRegistered = true;
    }
    eventBus.connect();
    void fetchUnreadCount();
  }

  function disconnect(): void {
    eventBus.disconnect();
  }

  function $reset(): void {
    items.value = [];
    total.value = 0;
    unreadCount.value = 0;
    error.value = null;
    filter.value = "all";
    typeFilter.value = new Set();
    loaded.value = false;
  }

  return {
    unreadCount,
    items,
    total,
    loading,
    loadingMore,
    error,
    filter,
    typeFilter,
    hasMore,
    fetchUnreadCount,
    load,
    loadMore,
    setFilter,
    toggleType,
    markSeen,
    markUnseen,
    markAllSeen,
    remove,
    clearAll,
    connect,
    disconnect,
    $reset,
  };
});
