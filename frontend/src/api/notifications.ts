import type { components } from "./types";
import { apiRequest, apiRequestWithHeaders } from "./client";

export type NotificationResponse =
  components["schemas"]["NotificationResponse"];
export type NotificationPreferenceItem =
  components["schemas"]["NotificationPreferenceItem"];
export type NotificationPreferencesResponse =
  components["schemas"]["NotificationPreferencesResponse"];

export type NotificationType =
  "follow" | "like" | "boost" | "quote" | "reply" | "mention" | "share";

export const NOTIFICATION_TYPES: NotificationType[] = [
  "follow",
  "like",
  "boost",
  "quote",
  "reply",
  "mention",
  "share",
];

export interface NotificationListPage {
  items: NotificationResponse[];
  total: number;
}

export async function listNotifications(params?: {
  limit?: number;
  offset?: number;
  seen?: boolean;
  type?: string;
}): Promise<NotificationListPage> {
  const response = await apiRequestWithHeaders<NotificationResponse[]>(
    "/notifications/",
    {
      query: {
        limit: params?.limit,
        offset: params?.offset,
        seen: params?.seen,
        type: params?.type,
      },
    },
  );
  const header = response.headers.get("X-Total-Count");
  const parsed = header === null ? NaN : Number(header);
  return {
    items: response.body,
    total: Number.isNaN(parsed) ? response.body.length : parsed,
  };
}

export async function getUnreadCount(): Promise<number> {
  const response = await apiRequest<
    components["schemas"]["UnreadCountResponse"]
  >("/notifications/unread-count");
  return response.count;
}

export function markSeen(ids: string[]): Promise<void> {
  return apiRequest<void>("/notifications/seen", {
    method: "POST",
    body: { ids },
  });
}

export function markUnseen(ids: string[]): Promise<void> {
  return apiRequest<void>("/notifications/unseen", {
    method: "POST",
    body: { ids },
  });
}

export function markAllSeen(): Promise<void> {
  return apiRequest<void>("/notifications/seen-all", { method: "POST" });
}

export function deleteNotification(id: string): Promise<void> {
  return apiRequest<void>(`/notifications/${id}`, { method: "DELETE" });
}

export async function deleteNotifications(ids: string[]): Promise<number> {
  const response = await apiRequest<components["schemas"]["DeletedResponse"]>(
    "/notifications/delete",
    {
      method: "POST",
      body: { ids },
    },
  );
  return response.deleted;
}

export async function clearNotifications(): Promise<number> {
  const response = await apiRequest<components["schemas"]["DeletedResponse"]>(
    "/notifications/clear",
    { method: "POST" },
  );
  return response.deleted;
}

export function getNotificationPreferences(): Promise<
  NotificationPreferenceItem[]
> {
  return apiRequest<NotificationPreferencesResponse>(
    "/notifications/preferences",
  ).then((response) => response.preferences);
}

export function updateNotificationPreferences(
  items: NotificationPreferenceItem[],
): Promise<NotificationPreferenceItem[]> {
  return apiRequest<NotificationPreferencesResponse>(
    "/notifications/preferences",
    {
      method: "PUT",
      body: { preferences: items },
    },
  ).then((response) => response.preferences);
}
