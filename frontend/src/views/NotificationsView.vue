<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type ComponentPublicInstance,
} from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { useNotificationsStore } from "@/stores/notifications";
import {
  NOTIFICATION_TYPES,
  type NotificationResponse,
} from "@/api/notifications";
import {
  lookupActivity,
  type ActivityMentionResponse,
  type ActivityResponse,
} from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { acceptFollowRequest, rejectFollowRequest } from "@/api/users";
import { useToastStore } from "@/stores/toast";
import { useDebounce } from "@/composables/useDebounce";
import { formatDateTime, formatRelativeTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import ContextMenu, { type MenuItem } from "@/components/ui/ContextMenu.vue";
import EntityActions, {
  type ActionItem,
} from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import NotificationActorCard from "@/components/notifications/NotificationActorCard.vue";
import NotificationActivityCard from "@/components/notifications/NotificationActivityCard.vue";
import NotificationItemCard from "@/components/notifications/NotificationItemCard.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { parseActorRef } from "@/utils/actorRef";
import { notificationActionText } from "@/utils/notifications";

const { t } = useI18n();
const store = useNotificationsStore();
const toast = useToastStore();

const TYPE_ICONS: Record<string, string> = {
  follow: "user-plus",
  like: "heart",
  boost: "retweet",
  quote: "quote-left",
  reply: "reply",
  mention: "at",
  share: "share-nodes",
  webmention: "link",
  activity: "bell",
  report: "flag",
};

const filters = [
  { key: "all" as const, label: t("notifications.filters.all") },
  { key: "unread" as const, label: t("notifications.filters.unread") },
];

const bulkMode = ref(false);
const selectedIds = ref<Set<string>>(new Set());
const clearAllOpen = ref(false);

let observer: IntersectionObserver | null = null;
const rowEls = new Map<string, HTMLElement>();
const pendingSeen = new Set<string>();
// Ids the user just toggled back to unseen stay excluded from the observer
// batch until they scroll out of the viewport and back in.
const manualUnseen = new Set<string>();

// Activity ids resolved lazily from note object URLs. Older notification
// payloads predate ``object_activity_id`` — when the note was materialized
// locally (remote replies) or authored here, the lookup returns its row so
// the card and the link point at ``/activities/{id}`` instead of a remote
// object id that may not dereference to a page.
const noteActivityIds = ref<Record<string, string | null>>({});
const noteLookups = new Map<string, Promise<ActivityResponse | null>>();

function lookupNoteActivity(url: string): Promise<ActivityResponse | null> {
  let pending = noteLookups.get(url);
  if (!pending) {
    pending = lookupActivity(url).catch(() => null);
    noteLookups.set(url, pending);
  }
  return pending;
}

async function resolveNoteActivities() {
  for (const item of store.items) {
    if (!NOTE_TYPES.has(item.type)) continue;
    if (item.id in noteActivityIds.value) continue;
    if (str(item.payload?.object_activity_id)) continue;
    const url = str(item.payload?.object_url) ?? item.source_url ?? undefined;
    if (!url || !/^https?:\/\//.test(url)) continue;
    noteActivityIds.value[item.id] = null;
    noteActivityIds.value[item.id] =
      (await lookupNoteActivity(url))?.id ?? null;
  }
}

watch(
  () => store.items.length,
  () => void resolveNoteActivities(),
);

const flushSeen = useDebounce(() => {
  const ids = [...pendingSeen];
  pendingSeen.clear();
  if (ids.length > 0) void store.markSeen(ids);
}, 500);

function isSeen(id: string): boolean {
  return store.items.some((item) => item.id === id && item.seen_at != null);
}

function onIntersect(entries: IntersectionObserverEntry[]) {
  for (const entry of entries) {
    const el = entry.target as HTMLElement;
    const id = el.dataset.notificationId;
    if (!id) continue;
    if (entry.isIntersecting) {
      if (manualUnseen.has(id) || isSeen(id)) continue;
      pendingSeen.add(id);
      observer?.unobserve(el);
      flushSeen();
    } else {
      manualUnseen.delete(id);
    }
  }
}

function setRowEl(el: Element | ComponentPublicInstance | null, id: string) {
  if (el instanceof HTMLElement) {
    rowEls.set(id, el);
    if (!isSeen(id)) observer?.observe(el);
  } else {
    const existing = rowEls.get(id);
    if (existing) observer?.unobserve(existing);
    rowEls.delete(id);
    pendingSeen.delete(id);
    manualUnseen.delete(id);
    selectedIds.value.delete(id);
  }
}

async function toggleSeen(item: NotificationResponse) {
  const el = rowEls.get(item.id);
  if (item.seen_at) {
    manualUnseen.add(item.id);
    if (el) observer?.observe(el);
    await store.markUnseen([item.id]);
  } else {
    manualUnseen.delete(item.id);
    pendingSeen.delete(item.id);
    if (el) observer?.unobserve(el);
    await store.markSeen([item.id]);
  }
}

async function dismiss(item: NotificationResponse) {
  await store.remove([item.id]);
}

// Pending follow requests are approved or declined straight from the
// notification body. The backend also pushes a ``notification_updated``
// event that swaps the buttons for the recorded outcome.
const resolvingRequests = ref<Set<string>>(new Set());

function followRequestPending(item: NotificationResponse): boolean {
  return (
    item.type === "follow" && item.payload?.follow_request_pending === true
  );
}

function followRequestStatus(item: NotificationResponse): string | undefined {
  return item.type === "follow"
    ? str(item.payload?.follow_request_status)
    : undefined;
}

async function decideFollowRequest(
  item: NotificationResponse,
  accept: boolean,
) {
  const actorUrl = item.actor_url;
  if (!actorUrl || resolvingRequests.value.has(item.id)) return;
  resolvingRequests.value = new Set([...resolvingRequests.value, item.id]);
  try {
    if (accept) {
      await acceptFollowRequest(actorUrl);
    } else {
      await rejectFollowRequest(actorUrl);
    }
    item.payload = {
      ...(item.payload ?? {}),
      follow_request_pending: false,
      follow_request_status: accept ? "accepted" : "rejected",
    };
  } catch (err) {
    toast.push({
      type: "error",
      message: t("notifications.followRequest.error", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    const next = new Set(resolvingRequests.value);
    next.delete(item.id);
    resolvingRequests.value = next;
  }
}

// Per-row overflow menu: mark read/unread and dismiss live behind a "…"
// trigger at the right of the notification's title row, Mastodon-style.
const menuNotification = ref<NotificationResponse | null>(null);
const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);

const menuItems = computed<MenuItem[]>(() => {
  const item = menuNotification.value;
  if (!item) return [];
  return [
    {
      key: "toggle-seen",
      label: item.seen_at
        ? t("notifications.markUnread")
        : t("notifications.markRead"),
      icon: item.seen_at ? "eye-slash" : "eye",
    },
    {
      key: "dismiss",
      label: t("notifications.dismiss"),
      icon: "xmark",
      danger: true,
    },
  ];
});

function openMenu(event: MouseEvent, item: NotificationResponse) {
  menuNotification.value = item;
  const trigger = event.currentTarget as HTMLElement | null;
  if (trigger) {
    const rect = trigger.getBoundingClientRect();
    menuX.value = Math.round(rect.right);
    menuY.value = Math.round(rect.bottom);
  } else {
    menuX.value = event.clientX;
    menuY.value = event.clientY;
  }
  menuOpen.value = true;
}

function closeMenu() {
  menuOpen.value = false;
  menuNotification.value = null;
}

function onMenuSelect(key: string) {
  const item = menuNotification.value;
  closeMenu();
  if (!item) return;
  if (key === "toggle-seen") void toggleSeen(item);
  else if (key === "dismiss") void dismiss(item);
}

function toggleBulkMode() {
  bulkMode.value = !bulkMode.value;
  if (!bulkMode.value) {
    selectedIds.value.clear();
  }
}

const allSelected = computed(
  () =>
    store.items.length > 0 &&
    store.items.every((item) => selectedIds.value.has(item.id)),
);

const someSelected = computed(
  () =>
    !allSelected.value &&
    store.items.some((item) => selectedIds.value.has(item.id)),
);

function toggleAll() {
  if (allSelected.value) {
    store.items.forEach((item) => selectedIds.value.delete(item.id));
  } else {
    store.items.forEach((item) => selectedIds.value.add(item.id));
  }
}

function toggleRow(item: NotificationResponse) {
  if (selectedIds.value.has(item.id)) {
    selectedIds.value.delete(item.id);
  } else {
    selectedIds.value.add(item.id);
  }
}

const bulkActions = computed<ActionItem[]>(() => [
  {
    key: "mark-read",
    label: t("notifications.markRead"),
    icon: "eye",
    variant: "secondary",
    disabled: selectedIds.value.size === 0,
  },
  {
    key: "mark-unread",
    label: t("notifications.markUnread"),
    icon: "eye-slash",
    variant: "secondary",
    disabled: selectedIds.value.size === 0,
  },
  {
    key: "delete",
    label: t("notifications.deleteSelected"),
    icon: "trash",
    variant: "danger",
    disabled: selectedIds.value.size === 0,
  },
]);

function onBulkAction(key: string) {
  const ids = [...selectedIds.value];
  if (key === "mark-read") {
    void store.markSeen(ids);
  } else if (key === "mark-unread") {
    void store.markUnseen(ids);
  } else if (key === "delete") {
    selectedIds.value.clear();
    void store.remove(ids);
  }
}

async function confirmClearAll() {
  clearAllOpen.value = false;
  selectedIds.value.clear();
  await store.clearAll();
}

function iconFor(type: string): string {
  return TYPE_ICONS[type] ?? "bell";
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function actorName(item: NotificationResponse): string {
  const payload = item.payload ?? {};
  const name = str(payload.actor_display_name) ?? str(payload.actor_name);
  return name ?? t("notifications.someone");
}

const URN_PREFIX = "urn:songhive:user:";
// ``activity`` notifications snapshot the authored activity's object (or,
// for authored likes/boosts, the reacted object) with the same
// ``object_*``/``target_*`` fields the note types carry.
const NOTE_TYPES = new Set([
  "mention",
  "reply",
  "quote",
  "webmention",
  "activity",
]);
// ``/users/{name}/objects/{id}`` permalinks are backend endpoints that
// redirect browsers to the right page — they must reach the server rather
// than the SPA router, which has no such route.
const OBJECT_PERMALINK_RE = /^\/users\/[^/]+\/objects\/[^/?#]+\/?$/;
const PLURAL_TO_ITEM_TYPE: Record<string, string> = {
  tracks: "track",
  albums: "album",
  artists: "artist",
  playlists: "playlist",
  libraries: "library",
  radios: "radio",
  files: "file",
};

interface NotificationLink {
  to?: string;
  href?: string;
}

const instanceDomain = useInstanceDomain();

// Activity ids whose fetch failed (deleted/unviewable): the row falls back
// to the entity item card or the actor card instead of retrying.
const failedActivityIds = ref<Set<string>>(new Set());

function onActivityCardError(notificationId: string) {
  failedActivityIds.value = new Set([
    ...failedActivityIds.value,
    notificationId,
  ]);
}

function rawLinkFor(item: NotificationResponse): string | undefined {
  const payload = item.payload ?? {};
  if (item.type === "report") {
    // Admin-only notifications — link straight to the reports queue.
    return "/admin/reports";
  }
  if (item.type === "follow") {
    // Object-scoped follows (thread subscriptions) link to the followed
    // object; plain actor follows link to the follower's profile.
    const target =
      str(payload.target_object_page_url) ??
      str(payload.target_local_url) ??
      str(payload.target_url);
    if (target) return target;
    const actor = item.actor_url || item.source_url || "";
    if (actor.startsWith(URN_PREFIX)) {
      return `/@${actor.slice(URN_PREFIX.length)}`;
    }
    return actor || undefined;
  }
  if (item.type === "like" || item.type === "boost") {
    // Prefer the reacted activity's own page, then the resolved entity
    // page, and finally the remote object URL.
    return (
      str(payload.object_page_url) ??
      str(payload.local_url) ??
      item.source_url ??
      undefined
    );
  }
  if (item.type === "reply" || item.type === "quote") {
    // "replied to your post" links to the *replied-to* activity — not to
    // the reply's own object id.
    const targetActivityId = str(payload.target_object_activity_id);
    if (targetActivityId) return `/activities/${targetActivityId}`;
    return (
      str(payload.target_object_page_url) ??
      str(payload.target_local_url) ??
      str(payload.target_url) ??
      str(payload.object_url) ??
      item.source_url ??
      undefined
    );
  }
  if (item.type === "mention" || item.type === "webmention") {
    const activityId =
      str(payload.object_activity_id) ?? noteActivityIds.value[item.id];
    if (activityId) return `/activities/${activityId}`;
    return str(payload.object_url) ?? item.source_url ?? undefined;
  }
  if (item.type === "activity") {
    // The authored activity's own page — for authored likes/boosts the
    // ``object_*`` fields resolve the reacted activity instead.
    const activityId =
      str(payload.object_activity_id) ?? noteActivityIds.value[item.id];
    if (activityId) return `/activities/${activityId}`;
    return (
      str(payload.object_page_url) ??
      str(payload.local_url) ??
      str(payload.object_url) ??
      item.source_url ??
      undefined
    );
  }
  return item.source_url ?? undefined;
}

function linkFor(item: NotificationResponse): NotificationLink | undefined {
  const raw = rawLinkFor(item);
  if (!raw) return undefined;
  if (raw.startsWith("/")) return { to: raw };
  try {
    const url = new URL(raw);
    if (url.host === window.location.host) {
      if (OBJECT_PERMALINK_RE.test(url.pathname)) return { href: raw };
      return { to: url.pathname + url.search + url.hash };
    }
    return { href: raw };
  } catch {
    return undefined;
  }
}

function actorLinkFor(
  item: NotificationResponse,
): NotificationLink | undefined {
  const parsed = parseActorRef(item.actor_url ?? "", instanceDomain.value);
  if (parsed.routeUsername) return { to: `/@${parsed.routeUsername}` };
  return undefined;
}

function noteMentions(payload: Record<string, unknown>) {
  // ``object_mentions`` snapshots the note's Mention tags so bare ``@handle``
  // text in the card content can link to the real actor URL.
  const raw = payload.object_mentions;
  if (!Array.isArray(raw)) return [];
  const mentions: ActivityMentionResponse[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const handle = str((entry as Record<string, unknown>).handle);
    if (!handle) continue;
    mentions.push({
      handle,
      actor_url: str((entry as Record<string, unknown>).actor_url) ?? null,
      user_id: null,
    });
  }
  return mentions;
}

function noteActivity(item: NotificationResponse): ActivityResponse | null {
  // Mentions/replies/quotes carry a snapshot of the remote note in the
  // payload — remote objects are not persisted as local Activity rows, so
  // the card renders read-only.
  if (!NOTE_TYPES.has(item.type)) return null;
  const payload = item.payload ?? {};
  const sourceId = str(payload.object_url) ?? item.source_url ?? "";
  if (!sourceId && !str(payload.object_content)) return null;
  const isQuote =
    item.type === "quote" ||
    (item.type === "activity" && str(payload.activity_type) === "quote");
  return {
    id: item.id,
    entity_type: "",
    entity_id: "",
    // Quote notifications render the quoter's note as a quote card: the
    // quoted activity — whose local id the payload carries under
    // ``target_object_activity_id`` — embeds inside it like any quote.
    activity_type: isQuote ? "quote" : "create",
    source_type: "remote",
    source_actor: item.actor_url ?? "",
    source_id: sourceId,
    local_object_id: null,
    object_url: str(payload.object_url) ?? null,
    owner_user_id: null,
    source_actor_avatar_url: str(payload.actor_avatar_url) ?? null,
    source_actor_display_name:
      str(payload.actor_display_name) ?? str(payload.actor_name) ?? null,
    visibility: "public",
    in_reply_to_activity_id: isQuote
      ? (str(payload.target_object_activity_id) ?? null)
      : null,
    content: str(payload.object_content) ?? str(payload.object_name) ?? null,
    content_source: null,
    content_type: "text/html",
    published_at:
      str(payload.published) ?? item.created_at ?? new Date(0).toISOString(),
    mentions: noteMentions(payload),
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    quote_count: 0,
    liked: false,
    boosted: false,
    can_interact: false,
  };
}

interface ItemContext {
  itemType: string;
  itemId: string;
  title?: string;
  to: string;
}

function itemContext(item: NotificationResponse): ItemContext | null {
  const payload = item.payload ?? {};
  let itemType = str(payload.item_type);
  let itemId = str(payload.item_id);
  // ``user`` has no item page — legacy payloads may still carry it, and
  // rendering it would link to a non-existent ``/users/{id}`` route.
  if (itemType === "user") {
    itemType = undefined;
    itemId = undefined;
  }
  if (!itemType || !itemId) {
    const candidate = str(payload.local_url) ?? item.source_url ?? "";
    const match = candidate.match(
      /^\/(tracks|albums|artists|playlists|libraries|radios|files)\/([^/?#]+)/,
    );
    if (!match) return null;
    itemType = PLURAL_TO_ITEM_TYPE[match[1]];
    itemId = match[2];
  }
  const plural = Object.keys(PLURAL_TO_ITEM_TYPE).find(
    (key) => PLURAL_TO_ITEM_TYPE[key] === itemType,
  );
  return {
    itemType,
    itemId,
    title: str(payload.item_title) ?? str(payload.track_title),
    to: `/${plural ?? `${itemType}s`}/${itemId}`,
  };
}

function activityRefFor(item: NotificationResponse): string | null {
  if (failedActivityIds.value.has(item.id)) return null;
  const payload = item.payload ?? {};
  if (NOTE_TYPES.has(item.type)) {
    // A note materialized locally — a remote reply stored as an activity
    // row, or a local status — renders its real card; unresolved remote
    // notes stay snapshot-only.
    return (
      str(payload.object_activity_id) ?? noteActivityIds.value[item.id] ?? null
    );
  }
  // Like/boost notifications carry the reacted activity's local id so the
  // row can render the real activity card. ``Audio`` objects (canonical
  // track shares) render as the track item card instead.
  if (item.type !== "like" && item.type !== "boost") return null;
  if (str(payload.object_type) === "Audio") return null;
  return str(payload.object_activity_id) ?? null;
}

function itemCardFor(item: NotificationResponse): ItemContext | null {
  if (
    item.type === "share" ||
    item.type === "like" ||
    item.type === "boost" ||
    item.type === "activity"
  ) {
    return itemContext(item);
  }
  return null;
}

function actorCardFor(item: NotificationResponse): boolean {
  // Follows always surface the actor; likes/boosts/activity fall back to
  // the actor card when neither the reacted activity nor a local item
  // resolves.
  if (item.type === "follow") return true;
  return (
    (item.type === "like" ||
      item.type === "boost" ||
      item.type === "activity") &&
    activityRefFor(item) === null &&
    itemContext(item) === null
  );
}

function targetContext(item: NotificationResponse):
  | (NotificationLink & {
      title: string;
    })
  | null {
  const payload = item.payload ?? {};
  // Object-scoped follows carry ``target_*`` fields for the followed
  // object — the same chip note types use for their reply/quote target.
  if (!NOTE_TYPES.has(item.type) && item.type !== "follow") return null;
  const to = str(payload.target_local_url);
  const title = str(payload.target_item_title);
  if (!to || !title) return null;
  return { to, title };
}

function actionText(item: NotificationResponse): string {
  return notificationActionText(item);
}

function relativeTime(value: string | null | undefined): string {
  return value ? formatRelativeTime(value) : "";
}

onMounted(() => {
  observer = new IntersectionObserver(onIntersect, { threshold: 0.5 });
  for (const [id, el] of rowEls) {
    if (!isSeen(id)) observer.observe(el);
  }
  void store.load().finally(() => void resolveNoteActivities());
});

onBeforeUnmount(() => {
  flushSeen.cancel();
  if (pendingSeen.size > 0) {
    void store.markSeen([...pendingSeen]);
    pendingSeen.clear();
  }
  observer?.disconnect();
  observer = null;
  rowEls.clear();
});
</script>

<template>
  <div class="notifications-view">
    <div class="notifications-view__header">
      <AppPageTitle class="notifications-view__title" icon="bell">{{
        t("notifications.title")
      }}</AppPageTitle>
      <div class="notifications-view__header-actions">
        <AppButton
          size="sm"
          variant="secondary"
          icon="check-double"
          :disabled="store.unreadCount === 0"
          @click="store.markAllSeen()"
        >
          {{ t("notifications.markAllRead") }}
        </AppButton>
        <AppButton
          size="sm"
          variant="secondary"
          icon="pen-to-square"
          :disabled="store.items.length === 0"
          @click="toggleBulkMode"
        >
          {{ bulkMode ? t("notifications.done") : t("notifications.select") }}
        </AppButton>
        <AppButton
          size="sm"
          variant="danger"
          icon="trash"
          :disabled="store.items.length === 0 && store.total === 0"
          @click="clearAllOpen = true"
        >
          {{ t("notifications.clearAll") }}
        </AppButton>
      </div>
    </div>

    <p
      v-if="store.unreadCount > 0"
      class="notifications-view__unread"
      aria-live="polite"
    >
      {{ t("notifications.unreadCount", store.unreadCount) }}
    </p>

    <div class="notifications-view__filters" role="group">
      <AppButton
        v-for="f in filters"
        :key="f.key"
        size="sm"
        :variant="store.filter === f.key ? 'primary' : 'ghost'"
        @click="store.setFilter(f.key)"
      >
        {{ f.label }}
      </AppButton>
    </div>

    <div
      class="notifications-view__filters notifications-view__filters--types"
      role="group"
      :aria-label="t('notifications.filters.types')"
    >
      <AppButton
        v-for="type in NOTIFICATION_TYPES"
        :key="type"
        size="sm"
        :icon="iconFor(type)"
        :variant="store.typeFilter.has(type) ? 'primary' : 'ghost'"
        :aria-pressed="store.typeFilter.has(type)"
        @click="store.toggleType(type)"
      >
        {{ t(`notifications.settings.typeNames.${type}`) }}
      </AppButton>
    </div>

    <div v-if="bulkMode" class="notifications-view__bulk">
      <AppCheckbox
        :model-value="allSelected"
        :indeterminate="someSelected"
        :label="t('notifications.selectAll')"
        @update:model-value="toggleAll"
      />
      <EntityActions
        :actions="bulkActions"
        :primary-count="3"
        @select="onBulkAction"
      />
    </div>

    <div v-if="store.error" class="notifications-view__error" role="alert">
      <span>{{ store.error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="store.load()">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <div
      v-else-if="store.loading && store.items.length === 0"
      class="notifications-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="store.items.length === 0" class="notifications-view__empty">
      {{
        store.filter === "unread"
          ? t("notifications.emptyUnread")
          : store.typeFilter.size > 0
            ? t("notifications.emptyFiltered")
            : t("notifications.empty")
      }}
    </div>

    <ul v-else class="notifications-view__list">
      <li
        v-for="item in store.items"
        :key="item.id"
        :ref="(el) => setRowEl(el, item.id)"
        class="notifications-view__row"
        :class="{
          'notifications-view__row--unseen': !item.seen_at,
          'notifications-view__row--selected': selectedIds.has(item.id),
        }"
        :data-notification-id="item.id"
      >
        <AppCheckbox
          v-if="bulkMode"
          :model-value="selectedIds.has(item.id)"
          :aria-label="t('notifications.selectRow')"
          @update:model-value="toggleRow(item)"
        />
        <div class="notifications-view__body">
          <div class="notifications-view__activity-header">
            <div class="notifications-view__activity-header--left">
              <AppIcon
                :name="iconFor(item.type)"
                class="notifications-view__icon"
                :title="item.type"
              />
              <span class="notifications-view__text">
                <RouterLink
                  v-if="actorLinkFor(item)?.to"
                  :to="actorLinkFor(item)!.to!"
                  class="notifications-view__actor"
                  ><strong>{{ actorName(item) }}</strong></RouterLink
                >
                <strong v-else>{{ actorName(item) }}</strong>
                {{ " " }}
                <RouterLink
                  v-if="linkFor(item)?.to"
                  :to="linkFor(item)!.to!"
                  class="notifications-view__action"
                  >{{ actionText(item) }}</RouterLink
                >
                <a
                  v-else-if="linkFor(item)?.href"
                  :href="linkFor(item)!.href"
                  target="_blank"
                  rel="noopener"
                  class="notifications-view__action"
                  >{{ actionText(item) }}</a
                >
                <template v-else>{{ actionText(item) }}</template>
              </span>
            </div>
            <div class="notifications-view__activity-header--right">
              <AppButton
                size="sm"
                variant="ghost"
                icon="ellipsis"
                class="notifications-view__menu-btn"
                :aria-label="t('common.openMenu')"
                :title="t('common.openMenu')"
                aria-haspopup="menu"
                :aria-expanded="menuOpen && menuNotification?.id === item.id"
                @click="openMenu($event, item)"
              />
            </div>
          </div>

          <NotificationActivityCard
            v-if="activityRefFor(item)"
            :activity-id="activityRefFor(item) || ''"
            class="notifications-view__card"
            @error="onActivityCardError(item.id)"
          />
          <ActivityCard
            v-else-if="noteActivity(item)"
            :activity="noteActivity(item)!"
            readonly
            class="notifications-view__card"
          />
          <NotificationItemCard
            v-else-if="itemCardFor(item)"
            :item-type="itemCardFor(item)!.itemType"
            :item-id="itemCardFor(item)!.itemId"
            :title="itemCardFor(item)!.title"
            :to="itemCardFor(item)!.to"
            class="notifications-view__card"
          />
          <NotificationActorCard
            v-else-if="actorCardFor(item)"
            :actor-url="item.actor_url"
            :display-name="
              str(item.payload?.actor_display_name) ??
              str(item.payload?.actor_name)
            "
            :avatar-url="str(item.payload?.actor_avatar_url)"
            class="notifications-view__card"
          />

          <div
            v-if="followRequestPending(item)"
            class="notifications-view__request-actions"
          >
            <AppButton
              size="sm"
              icon="check"
              :disabled="resolvingRequests.has(item.id)"
              @click="decideFollowRequest(item, true)"
            >
              {{ t("notifications.followRequest.accept") }}
            </AppButton>
            <AppButton
              size="sm"
              variant="danger"
              icon="xmark"
              :disabled="resolvingRequests.has(item.id)"
              @click="decideFollowRequest(item, false)"
            >
              {{ t("notifications.followRequest.reject") }}
            </AppButton>
          </div>
          <span
            v-else-if="followRequestStatus(item)"
            class="notifications-view__request-status"
          >
            {{ t(`notifications.followRequest.${followRequestStatus(item)}`) }}
          </span>

          <div class="notifications-view__meta">
            <RouterLink
              v-if="targetContext(item)"
              :to="targetContext(item)!.to!"
              class="notifications-view__target"
              >{{
                t("notifications.onItem", { title: targetContext(item)!.title })
              }}</RouterLink
            >
            <time
              class="notifications-view__time"
              :datetime="item.created_at || undefined"
              :title="formatDateTime(item.created_at)"
              >{{ relativeTime(item.created_at) }}</time
            >
          </div>
        </div>
      </li>
    </ul>

    <div
      v-if="!store.error && store.hasMore"
      class="notifications-view__footer"
    >
      <AppButton
        icon="chevron-down"
        variant="secondary"
        :loading="store.loadingMore"
        :disabled="store.loading"
        @click="store.loadMore()"
      >
        {{ t("notifications.loadMore") }}
      </AppButton>
    </div>

    <ContextMenu
      :open="menuOpen"
      :items="menuItems"
      :x="menuX"
      :y="menuY"
      @select="onMenuSelect"
      @close="closeMenu"
    />

    <AppModal
      :open="clearAllOpen"
      :title="t('notifications.clearAllTitle')"
      @close="clearAllOpen = false"
    >
      <p>{{ t("notifications.clearAllConfirm") }}</p>
      <template #actions>
        <AppButton variant="secondary" @click="clearAllOpen = false">
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton variant="danger" icon="trash" @click="confirmClearAll">
          {{ t("notifications.clearAll") }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;

  --notification-btn-width: 2.5rem;
}

.notifications-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.notifications-view__header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.notifications-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.notifications-view__unread {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.notifications-view__filters {
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
}

.notifications-view__bulk {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.notifications-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.notifications-view__skeleton {
  min-height: 16rem;
}

.notifications-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.notifications-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

@media (max-width: 767px) {
  .notifications-view__list {
    margin: 0 calc(-1 * var(--space-4));
  }
}

.notifications-view__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
}

@media (max-width: 767px) {
  .notifications-view__row {
    padding: var(--space-3) 0;
  }
}

.notifications-view__row--unseen {
  background-color: var(--color-surface-active);
}

.notifications-view__row--selected {
  background-color: var(--color-surface);
}

.notifications-view__icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
  width: 1.25rem;
  text-align: center;
  margin-right: var(--space-2);
}

.notifications-view__row--unseen .notifications-view__icon {
  color: var(--color-text-secondary);
}

.notifications-view__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1);
  color: var(--color-text);
}

.notifications-view__activity-header {
  width: 100%;
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}

.notifications-view__activity-header--left {
  flex: 1;
}

@media (max-width: 767px) {
  .notifications-view__activity-header--left {
    padding: 0 var(--space-1);
  }
}

.notifications-view__activity-header--right {
  width: var(--notification-btn-width);
}

.notifications-view__text {
  flex: 1;
  min-width: 0;
  color: var(--color-text);
  text-decoration: none;
}

.notifications-view__menu-btn {
  color: var(--color-text-muted);
}

.notifications-view__actor,
.notifications-view__action {
  color: inherit;
  text-decoration: none;
}

.notifications-view__actor:hover,
.notifications-view__action:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

/* ActivityCard widens itself on large screens for the feed; inside a
   notification row it must fit the row instead. */
.notifications-view__row .notifications-view__card {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}

.notifications-view__request-actions {
  width: 100%;
  display: flex;
  gap: var(--space-2);
}

.notifications-view__request-status {
  width: 100%;
  color: var(--color-text-muted);
  font-size: 0.8125rem;
}

.notifications-view__meta {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.notifications-view__target {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-decoration: none;
}

.notifications-view__target:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

.notifications-view__time {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
}

.notifications-view__footer {
  display: flex;
  justify-content: center;
}

@media (max-width: 767px) {
  .notifications-view__time,
  .notifications-view__target {
    padding: 0 var(--space-2);
  }
}
</style>
