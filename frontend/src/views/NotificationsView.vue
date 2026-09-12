<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  type ComponentPublicInstance,
} from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { useNotificationsStore } from "@/stores/notifications";
import {
  NOTIFICATION_TYPES,
  type NotificationResponse,
} from "@/api/notifications";
import type {
  ActivityMentionResponse,
  ActivityResponse,
} from "@/api/activities";
import { useDebounce } from "@/composables/useDebounce";
import { formatDateTime, formatRelativeTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import EntityActions, {
  type ActionItem,
} from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import NotificationActorCard from "@/components/notifications/NotificationActorCard.vue";
import NotificationItemCard from "@/components/notifications/NotificationItemCard.vue";

const { t } = useI18n();
const store = useNotificationsStore();

const TYPE_ICONS: Record<string, string> = {
  follow: "user-plus",
  like: "heart",
  boost: "retweet",
  quote: "quote-left",
  reply: "reply",
  mention: "at",
  share: "share-nodes",
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
const NOTE_TYPES = new Set(["mention", "reply", "quote"]);
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

function rawLinkFor(item: NotificationResponse): string | undefined {
  const payload = item.payload ?? {};
  if (item.type === "follow") {
    const actor = item.actor_url || item.source_url || "";
    if (actor.startsWith(URN_PREFIX)) {
      return `/@${actor.slice(URN_PREFIX.length)}`;
    }
    return actor || undefined;
  }
  if (item.type === "like" || item.type === "boost") {
    // Prefer the resolved local page; fall back to the remote object URL.
    return str(payload.local_url) ?? item.source_url ?? undefined;
  }
  if (NOTE_TYPES.has(item.type)) {
    return str(payload.object_url) ?? item.source_url ?? undefined;
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
      return { to: url.pathname + url.search + url.hash };
    }
    return { href: raw };
  } catch {
    return undefined;
  }
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
  return {
    id: item.id,
    entity_type: "",
    entity_id: "",
    activity_type: "create",
    source_type: "remote",
    source_actor: item.actor_url ?? "",
    source_id: sourceId,
    local_object_id: null,
    owner_user_id: null,
    source_actor_avatar_url: str(payload.actor_avatar_url) ?? null,
    source_actor_display_name:
      str(payload.actor_display_name) ?? str(payload.actor_name) ?? null,
    visibility: "public",
    in_reply_to_activity_id: null,
    content: str(payload.object_content) ?? str(payload.object_name) ?? null,
    content_source: null,
    content_type: "text/html",
    published_at:
      str(payload.published) ?? item.created_at ?? new Date(0).toISOString(),
    mentions: noteMentions(payload),
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

function itemCardFor(item: NotificationResponse): ItemContext | null {
  if (item.type === "share" || item.type === "like" || item.type === "boost") {
    return itemContext(item);
  }
  return null;
}

function actorCardFor(item: NotificationResponse): boolean {
  // Follows always surface the actor; likes/boosts fall back to the actor
  // card when the referenced object is not a local item.
  if (item.type === "follow") return true;
  return (
    (item.type === "like" || item.type === "boost") &&
    itemContext(item) === null
  );
}

function targetContext(item: NotificationResponse):
  | (NotificationLink & {
      title: string;
    })
  | null {
  const payload = item.payload ?? {};
  if (!NOTE_TYPES.has(item.type)) return null;
  const to = str(payload.target_local_url);
  const title = str(payload.target_item_title);
  if (!to || !title) return null;
  return { to, title };
}

function actionText(item: NotificationResponse): string {
  const key = `notifications.types.${item.type}`;
  const fallback = t("notifications.types.unknown");
  const translated = t(key);
  return translated === key ? fallback : translated;
}

function relativeTime(value: string | null | undefined): string {
  return value ? formatRelativeTime(value) : "";
}

onMounted(() => {
  observer = new IntersectionObserver(onIntersect, { threshold: 0.5 });
  for (const [id, el] of rowEls) {
    if (!isSeen(id)) observer.observe(el);
  }
  void store.load();
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
            <AppIcon
              :name="iconFor(item.type)"
              class="notifications-view__icon"
              :title="item.type"
            />
            <RouterLink
              v-if="linkFor(item)?.to"
              :to="linkFor(item)!.to!"
              class="notifications-view__text"
            >
              <strong>{{ actorName(item) }}</strong>
              {{ actionText(item) }}
            </RouterLink>
            <a
              v-else-if="linkFor(item)?.href"
              :href="linkFor(item)!.href"
              target="_blank"
              rel="noopener"
              class="notifications-view__text"
            >
              <strong>{{ actorName(item) }}</strong>
              {{ actionText(item) }}
            </a>
            <span v-else class="notifications-view__text">
              <strong>{{ actorName(item) }}</strong>
              {{ actionText(item) }}
            </span>
          </div>

          <ActivityCard
            v-if="noteActivity(item)"
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
        <div class="notifications-view__actions">
          <AppButton
            size="sm"
            variant="ghost"
            :icon="item.seen_at ? 'eye-slash' : 'eye'"
            :aria-label="
              item.seen_at
                ? t('notifications.markUnread')
                : t('notifications.markRead')
            "
            :title="
              item.seen_at
                ? t('notifications.markUnread')
                : t('notifications.markRead')
            "
            @click="toggleSeen(item)"
          />
          <AppButton
            size="sm"
            variant="ghost"
            icon="xmark"
            :aria-label="t('notifications.dismiss')"
            :title="t('notifications.dismiss')"
            @click="dismiss(item)"
          />
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

.notifications-view__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
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
}

.notifications-view__row--unseen .notifications-view__icon {
  color: var(--color-text-secondary);
}

.notifications-view__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  color: var(--color-text);
}

.notifications-view__activity-header {
  display: inline-flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}

.notifications-view__text {
  color: var(--color-text);
  text-decoration: none;
}

.notifications-view__text:is(a):hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

.notifications-view__actions {
  display: flex;
  gap: var(--space-2);
}

@media (max-width: 767px) {
  .notifications-view__actions {
    flex-direction: column;
  }
}

/* ActivityCard widens itself on large screens for the feed; inside a
   notification row it must fit the row instead. */
.notifications-view__row .notifications-view__card {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
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
</style>
