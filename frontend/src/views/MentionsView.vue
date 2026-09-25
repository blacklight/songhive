<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  listMentions,
  MENTION_SOURCES,
  type MentionResponse,
  type MentionSource,
  type MentionVisibilityFilter,
} from "@/api/mentions";
import type { NotificationResponse } from "@/api/notifications";
import {
  lookupActivity,
  type ActivityMentionResponse,
  type ActivityResponse,
  type ActivityVisibility,
} from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { formatDateTime, formatRelativeTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import NotificationActorCard from "@/components/notifications/NotificationActorCard.vue";
import NotificationActivityCard from "@/components/notifications/NotificationActivityCard.vue";
import NotificationItemCard from "@/components/notifications/NotificationItemCard.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { parseActorRef } from "@/utils/actorRef";
import { notificationActionText } from "@/utils/notifications";

const { t } = useI18n();
const PAGE_SIZE = 20;

const SOURCE_ICONS: Record<string, string> = {
  local: "house",
  activitypub: "globe",
  webmention: "link",
};

type SourceFilter = MentionSource | "all";

const sourceFilter = ref<SourceFilter>("all");
const visibilityFilter = ref<MentionVisibilityFilter>("all");

const sourceFilters: { key: SourceFilter; label: string }[] = [
  { key: "all", label: t("mentions.filters.all") },
  ...MENTION_SOURCES.map((source) => ({
    key: source as SourceFilter,
    label: t(`mentions.sources.${source}`),
  })),
];

const visibilityFilters: { key: MentionVisibilityFilter; label: string }[] = [
  { key: "all", label: t("mentions.filters.all") },
  { key: "private", label: t("mentions.filters.private") },
];

const items = ref<MentionResponse[]>([]);
const total = ref(0);
const loading = ref(false);
const loadingMore = ref(false);
const loaded = ref(false);
const error = ref<string | null>(null);

const hasMore = computed(() => items.value.length < total.value);

function sourceParam(): string | undefined {
  return sourceFilter.value === "all" ? undefined : sourceFilter.value;
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  error.value = null;
  try {
    const page = await listMentions({
      limit: PAGE_SIZE,
      offset: 0,
      source: sourceParam(),
      visibility: visibilityFilter.value,
    });
    items.value = page.items;
    total.value = page.total;
    loaded.value = true;
  } catch (err) {
    error.value = t("mentions.errors.loadFailed", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (loadingMore.value || loading.value || !hasMore.value) return;
  loadingMore.value = true;
  try {
    const page = await listMentions({
      limit: PAGE_SIZE,
      offset: items.value.length,
      source: sourceParam(),
      visibility: visibilityFilter.value,
    });
    items.value = [...items.value, ...page.items];
    total.value = page.total;
  } catch (err) {
    error.value = t("mentions.errors.loadFailed", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    loadingMore.value = false;
  }
}

function setSourceFilter(key: SourceFilter) {
  sourceFilter.value = key;
}

function setVisibilityFilter(key: MentionVisibilityFilter) {
  visibilityFilter.value = key;
}

watch([sourceFilter, visibilityFilter], () => void load());

// A record renders through the notification card pipeline — its payload
// snapshots the same fields the matching notification carried.
function asItem(record: MentionResponse): NotificationResponse {
  return {
    id: record.id,
    type: record.source === "webmention" ? "webmention" : "mention",
    actor_url: record.actor_url ?? null,
    actor_handle: record.actor_handle ?? null,
    source_url: record.source_url ?? null,
    payload: record.payload ?? null,
    seen_at: null,
    created_at: record.created_at ?? null,
  };
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

function actorName(record: MentionResponse): string {
  const payload = record.payload ?? {};
  const name = str(payload.actor_display_name) ?? str(payload.actor_name);
  return name ?? t("notifications.someone");
}

// Activity ids resolved lazily from note object URLs for records whose
// payload predates ``object_activity_id`` and that never materialized.
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
  for (const record of items.value) {
    if (record.id in noteActivityIds.value) continue;
    if (record.activity_id) continue;
    if (str(record.payload?.object_activity_id)) continue;
    const url =
      str(record.payload?.object_url) ?? record.source_url ?? undefined;
    if (!url || !/^https?:\/\//.test(url)) continue;
    noteActivityIds.value[record.id] = null;
    noteActivityIds.value[record.id] =
      (await lookupNoteActivity(url))?.id ?? null;
  }
}

watch(items, () => void resolveNoteActivities());

// Activity ids whose fetch failed (deleted/unviewable): the row falls back
// to the snapshot card or the actor card instead of retrying.
const failedActivityIds = ref<Set<string>>(new Set());

function onActivityCardError(recordId: string) {
  failedActivityIds.value = new Set([...failedActivityIds.value, recordId]);
}

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

interface MentionLink {
  to?: string;
  href?: string;
}

const instanceDomain = useInstanceDomain();

function rawLinkFor(record: MentionResponse): string | undefined {
  const payload = record.payload ?? {};
  const activityId = activityRefFor(record);
  if (activityId) return `/activities/${activityId}`;
  return str(payload.object_url) ?? record.source_url ?? undefined;
}

function linkFor(record: MentionResponse): MentionLink | undefined {
  const raw = rawLinkFor(record);
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

function actorLinkFor(record: MentionResponse): MentionLink | undefined {
  // A server-resolved ``actor_handle`` routes to the internal profile —
  // opaque actor ids carry no username in their URL tail, and the raw actor
  // URI may not dereference to a browser page.
  const handle = str(record.actor_handle);
  if (handle) return { to: `/@${handle}` };
  const parsed = parseActorRef(record.actor_url ?? "", instanceDomain.value);
  if (parsed.username) return { to: `/@${parsed.username}` };
  if (parsed.remoteUrl) return { href: parsed.remoteUrl };
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

const ACTIVITY_VISIBILITIES: ActivityVisibility[] = [
  "public",
  "followers",
  "mentioned",
  "local",
  "private",
];

function activityVisibility(
  value: string | null | undefined,
): ActivityVisibility {
  return (ACTIVITY_VISIBILITIES as string[]).includes(value ?? "")
    ? (value as ActivityVisibility)
    : "public";
}

function noteActivity(record: MentionResponse): ActivityResponse | null {
  // Records carry a snapshot of the mentioning object in the payload —
  // remote notes are not persisted as local Activity rows, so the card
  // renders read-only.
  const payload = record.payload ?? {};
  const sourceId = str(payload.object_url) ?? record.source_url ?? "";
  if (!sourceId && !str(payload.object_content)) return null;
  return {
    id: record.id,
    entity_type: "",
    entity_id: "",
    activity_type: "create",
    source_type: "remote",
    source_actor: record.actor_url ?? "",
    source_id: sourceId,
    local_object_id: null,
    object_url: str(payload.object_url) ?? null,
    owner_user_id: null,
    source_actor_avatar_url: str(payload.actor_avatar_url) ?? null,
    source_actor_display_name:
      str(payload.actor_display_name) ?? str(payload.actor_name) ?? null,
    source_actor_handle:
      str(payload.actor_handle) ?? str(record.actor_handle) ?? null,
    visibility: activityVisibility(record.visibility),
    in_reply_to_activity_id: null,
    content: str(payload.object_content) ?? str(payload.object_name) ?? null,
    content_source: null,
    content_type: "text/html",
    published_at:
      str(payload.published) ?? record.created_at ?? new Date(0).toISOString(),
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

function itemContext(record: MentionResponse): ItemContext | null {
  const payload = record.payload ?? {};
  let itemType = str(payload.item_type);
  let itemId = str(payload.item_id);
  // ``user`` has no item page — legacy payloads may still carry it, and
  // rendering it would link to a non-existent ``/users/{id}`` route.
  if (itemType === "user") {
    itemType = undefined;
    itemId = undefined;
  }
  if (!itemType || !itemId) {
    const candidate = str(payload.local_url) ?? record.source_url ?? "";
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

function activityRefFor(record: MentionResponse): string | null {
  if (failedActivityIds.value.has(record.id)) return null;
  // A mention materialized locally — a remote reply stored as an activity
  // row, a local status, or a Webmention activity — renders its real card;
  // unresolved remote notes stay snapshot-only.
  return (
    record.activity_id ??
    str(record.payload?.object_activity_id) ??
    noteActivityIds.value[record.id] ??
    null
  );
}

function targetContext(record: MentionResponse):
  | (MentionLink & {
      title: string;
    })
  | null {
  const payload = record.payload ?? {};
  const to = str(payload.target_local_url);
  const title = str(payload.target_item_title);
  if (!to || !title) return null;
  return { to, title };
}

function webmentionTypeLabel(record: MentionResponse): string | null {
  if (record.source !== "webmention") return null;
  const type = str(record.payload?.webmention_type) ?? "mention";
  return t(`activities.webmentionTypes.${type}`);
}

function isPrivate(record: MentionResponse): boolean {
  return (record.visibility ?? "public") !== "public";
}

function sourceIcon(record: MentionResponse): string {
  return SOURCE_ICONS[record.source] ?? "at";
}

function actionText(record: MentionResponse): string {
  return notificationActionText(asItem(record));
}

function relativeTime(value: string | null | undefined): string {
  return value ? formatRelativeTime(value) : "";
}

onMounted(() => {
  void load().finally(() => void resolveNoteActivities());
});
</script>

<template>
  <div class="mentions-view">
    <div class="mentions-view__header">
      <AppPageTitle class="mentions-view__title" icon="at">{{
        t("mentions.title")
      }}</AppPageTitle>
    </div>

    <div
      class="mentions-view__filters"
      role="group"
      :aria-label="t('mentions.filters.sources')"
    >
      <AppButton
        v-for="f in sourceFilters"
        :key="f.key"
        size="sm"
        :icon="f.key === 'all' ? undefined : SOURCE_ICONS[f.key]"
        :variant="sourceFilter === f.key ? 'primary' : 'ghost'"
        :aria-pressed="sourceFilter === f.key"
        @click="setSourceFilter(f.key)"
      >
        {{ f.label }}
      </AppButton>
    </div>

    <div
      class="mentions-view__filters"
      role="group"
      :aria-label="t('mentions.filters.visibility')"
    >
      <AppButton
        v-for="f in visibilityFilters"
        :key="f.key"
        size="sm"
        :icon="f.key === 'private' ? 'lock' : undefined"
        :variant="visibilityFilter === f.key ? 'primary' : 'ghost'"
        :aria-pressed="visibilityFilter === f.key"
        @click="setVisibilityFilter(f.key)"
      >
        {{ f.label }}
      </AppButton>
    </div>

    <div v-if="error" class="mentions-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load()">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <div
      v-else-if="loading && items.length === 0"
      class="mentions-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="items.length === 0" class="mentions-view__empty">
      {{
        sourceFilter !== "all" || visibilityFilter !== "all"
          ? t("mentions.emptyFiltered")
          : t("mentions.empty")
      }}
    </div>

    <ul v-else class="mentions-view__list">
      <li
        v-for="record in items"
        :key="record.id"
        class="mentions-view__row"
        :data-mention-id="record.id"
      >
        <div class="mentions-view__body">
          <div class="mentions-view__activity-header">
            <div class="mentions-view__activity-header--left">
              <AppIcon
                :name="sourceIcon(record)"
                class="mentions-view__icon"
                :title="t(`mentions.sources.${record.source}`)"
              />
              <span class="mentions-view__text">
                <RouterLink
                  v-if="actorLinkFor(record)?.to"
                  :to="actorLinkFor(record)!.to!"
                  class="mentions-view__actor"
                  ><strong>{{ actorName(record) }}</strong></RouterLink
                >
                <a
                  v-else-if="actorLinkFor(record)?.href"
                  :href="actorLinkFor(record)!.href"
                  target="_blank"
                  rel="noopener"
                  class="mentions-view__actor"
                  ><strong>{{ actorName(record) }}</strong></a
                >
                <strong v-else>{{ actorName(record) }}</strong>
                {{ " " }}
                <RouterLink
                  v-if="linkFor(record)?.to"
                  :to="linkFor(record)!.to!"
                  class="mentions-view__action"
                  >{{ actionText(record) }}</RouterLink
                >
                <a
                  v-else-if="linkFor(record)?.href"
                  :href="linkFor(record)!.href"
                  target="_blank"
                  rel="noopener"
                  class="mentions-view__action"
                  >{{ actionText(record) }}</a
                >
                <template v-else>{{ actionText(record) }}</template>
              </span>
            </div>
          </div>

          <NotificationActivityCard
            v-if="activityRefFor(record)"
            :activity-id="activityRefFor(record) || ''"
            class="mentions-view__card"
            @error="onActivityCardError(record.id)"
          />
          <ActivityCard
            v-else-if="noteActivity(record)"
            :activity="noteActivity(record)!"
            readonly
            class="mentions-view__card"
          />
          <NotificationItemCard
            v-else-if="itemContext(record)"
            :item-type="itemContext(record)!.itemType"
            :item-id="itemContext(record)!.itemId"
            :title="itemContext(record)!.title"
            :to="itemContext(record)!.to"
            class="mentions-view__card"
          />
          <NotificationActorCard
            v-else
            :actor-url="record.actor_url"
            :handle="record.actor_handle"
            :display-name="
              str(record.payload?.actor_display_name) ??
              str(record.payload?.actor_name)
            "
            :avatar-url="str(record.payload?.actor_avatar_url)"
            class="mentions-view__card"
          />

          <div class="mentions-view__meta">
            <span class="mentions-view__badges">
              <span class="mentions-view__badge">{{
                t(`mentions.sources.${record.source}`)
              }}</span>
              <span
                v-if="webmentionTypeLabel(record)"
                class="mentions-view__badge"
                >{{ webmentionTypeLabel(record) }}</span
              >
              <span
                v-if="isPrivate(record)"
                class="mentions-view__badge mentions-view__badge--private"
                ><AppIcon name="lock" /> {{ t("mentions.private") }}</span
              >
            </span>
            <RouterLink
              v-if="targetContext(record)"
              :to="targetContext(record)!.to!"
              class="mentions-view__target"
              >{{
                t("notifications.onItem", {
                  title: targetContext(record)!.title,
                })
              }}</RouterLink
            >
            <time
              class="mentions-view__time"
              :datetime="record.created_at || undefined"
              :title="formatDateTime(record.created_at)"
              >{{ relativeTime(record.created_at) }}</time
            >
          </div>
        </div>
      </li>
    </ul>

    <div v-if="!error && hasMore" class="mentions-view__footer">
      <AppButton
        icon="chevron-down"
        variant="secondary"
        :loading="loadingMore"
        :disabled="loading"
        @click="loadMore()"
      >
        {{ t("mentions.loadMore") }}
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.mentions-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;
}

.mentions-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.mentions-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.mentions-view__filters {
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
}

.mentions-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.mentions-view__skeleton {
  min-height: 16rem;
}

.mentions-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.mentions-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

@media (max-width: 767px) {
  .mentions-view__list {
    margin: 0 calc(-1 * var(--space-4));
  }
}

.mentions-view__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
}

@media (max-width: 767px) {
  .mentions-view__row {
    padding: var(--space-3) 0;
  }
}

.mentions-view__icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
  width: 1.25rem;
  text-align: center;
  margin-right: var(--space-2);
}

.mentions-view__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1);
  color: var(--color-text);
}

.mentions-view__activity-header {
  width: 100%;
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}

.mentions-view__activity-header--left {
  flex: 1;
}

@media (max-width: 767px) {
  .mentions-view__activity-header--left {
    padding: 0 var(--space-1);
  }
}

.mentions-view__text {
  flex: 1;
  min-width: 0;
  color: var(--color-text);
  text-decoration: none;
}

.mentions-view__actor,
.mentions-view__action {
  color: inherit;
  text-decoration: none;
}

.mentions-view__actor:hover,
.mentions-view__action:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

/* ActivityCard widens itself on large screens for the feed; inside a
   mention row it must fit the row instead. */
.mentions-view__row .mentions-view__card {
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}

.mentions-view__meta {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.mentions-view__badges {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.mentions-view__badge {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-1);
}

.mentions-view__badge--private {
  color: var(--color-text-secondary);
}

.mentions-view__target {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  text-decoration: none;
}

.mentions-view__target:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

.mentions-view__time {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
}

.mentions-view__footer {
  display: flex;
  justify-content: center;
}

@media (max-width: 767px) {
  .mentions-view__time,
  .mentions-view__target {
    padding: 0 var(--space-2);
  }
}
</style>
