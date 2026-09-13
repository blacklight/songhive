import { i18n } from "@/i18n";
import type { NotificationResponse } from "@/api/notifications";

/**
 * Entity kinds a notification's action text can name. ``post`` covers
 * statuses (``Note`` objects) and unresolved remote objects; the rest are
 * the library entities other users can interact with.
 */
export type NotificationObjectKind =
  | "post"
  | "track"
  | "album"
  | "artist"
  | "playlist"
  | "library"
  | "radio"
  | "file";

const ITEM_KINDS = new Set<string>([
  "track",
  "album",
  "artist",
  "playlist",
  "library",
  "radio",
  "file",
]);

function str(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function itemKind(value: unknown): NotificationObjectKind | undefined {
  const itemType = str(value);
  return itemType !== undefined && ITEM_KINDS.has(itemType)
    ? (itemType as NotificationObjectKind)
    : undefined;
}

function objectKind(
  payload: Record<string, unknown>,
  prefix: "" | "target_",
): NotificationObjectKind {
  // An ``Audio`` object is the track itself; any other activity object
  // (``Note``, …) is a post. When no activity object is recorded, the
  // resolved item — a favorite, an entity page URL — is what was
  // interacted with.
  if (str(payload[`${prefix}object_type`]) === "Audio") return "track";
  if (
    str(payload[`${prefix}object_type`]) !== undefined ||
    str(payload[`${prefix}object_activity_id`]) !== undefined
  ) {
    return "post";
  }
  return itemKind(payload[`${prefix}item_type`]) ?? "post";
}

/**
 * Return the entity kind a notification's action text should name.
 *
 * Likes and boosts name the reacted object, shares the granted item;
 * replies and quotes name the ``target_*`` object — their ``object_*``
 * fields snapshot the reply or quote note itself. Follows and mentions
 * name no object (their texts ignore the parameter).
 */
export function notificationObjectKind(
  notification: Pick<NotificationResponse, "type" | "payload">,
): NotificationObjectKind {
  const payload = notification.payload ?? {};
  const prefix =
    notification.type === "reply" || notification.type === "quote"
      ? "target_"
      : "";
  return objectKind(payload, prefix);
}

/**
 * Return the translated action line of a notification ("liked your
 * track", "replied to your post", …), falling back to the generic
 * placeholder for unknown types.
 */
export function notificationActionText(
  notification: Pick<NotificationResponse, "type" | "payload">,
): string {
  const key = `notifications.types.${notification.type}`;
  // ``share`` phrases its object with an indefinite article ("shared a
  // track with you"); the other types use the bare entity name.
  const objectsKey =
    notification.type === "share" ? "objectsWithArticle" : "objects";
  const object = i18n.global.t(
    `notifications.${objectsKey}.${notificationObjectKind(notification)}`,
  );
  const translated = i18n.global.t(key, { object });
  return translated === key
    ? i18n.global.t("notifications.types.unknown")
    : translated;
}
