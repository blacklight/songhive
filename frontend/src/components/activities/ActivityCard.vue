<script setup lang="ts">
import { RouterLink, useRouter } from "vue-router";
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listActivityQuotes,
  listActivityReplies,
  type ActivityResponse,
  type RemoteQuote,
  type RemoteReply,
} from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { useActivitiesStore } from "@/stores/activities";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import StatusComposer, {
  type StatusComposerPayload,
} from "@/components/statuses/StatusComposer.vue";
import ActivityActorsModal from "./ActivityActorsModal.vue";
import ActivityEditModal from "./ActivityEditModal.vue";
import ActivityObjectEmbed from "./ActivityObjectEmbed.vue";
import ActivityRemoteReply from "./ActivityRemoteReply.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { parseActorRef } from "@/utils/actorRef";
import {
  parseActivityContent,
  type ContentMark,
  type ContentSegment,
} from "@/utils/activityContent";

const props = withDefaults(
  defineProps<{
    activity: ActivityResponse;
    readonly?: boolean;
    expandReplies?: boolean;
  }>(),
  { readonly: false, expandReplies: false },
);

const { t } = useI18n();
const router = useRouter();
const store = useActivitiesStore();
const authStore = useAuthStore();
const confirmStore = useConfirmStore();
const toast = useToastStore();
const instanceDomain = useInstanceDomain();

const editOpen = ref(false);

const URN_PREFIX = "urn:songhive:user:";

const TYPE_ICONS: Record<string, string> = {
  like: "heart",
  announce: "retweet",
  reply: "reply",
  quote: "quote-left",
  mention: "at",
  update: "pen-to-square",
  delete: "trash",
  webmention: "link",
};

const VISIBILITY_ICONS: Record<string, string> = {
  public: "globe",
  followers: "user-group",
  mentioned: "at",
  local: "house",
  private: "lock",
};

function parseActor(actor: string): { shortName: string; host?: string } {
  if (actor.startsWith(URN_PREFIX)) {
    return { shortName: actor.slice(URN_PREFIX.length) };
  }
  try {
    const url = new URL(actor);
    const shortName =
      url.pathname.split("/").filter(Boolean).pop() ?? url.hostname;
    return { shortName, host: url.hostname };
  } catch {
    return { shortName: actor };
  }
}

// The freshest copy of this activity: cards rendered from lists that are
// not backed by the store's ``items`` (profile tabs, tag detail,
// notifications) would otherwise show stale content after an edit.
const activity = computed(
  () => store.updatedActivity(props.activity.id) ?? props.activity,
);
const removed = computed(() => store.isRemoved(props.activity.id));

const actorShortName = computed(
  () => parseActor(activity.value.source_actor).shortName,
);

const actorName = computed(() => {
  const { shortName, host } = parseActor(activity.value.source_actor);
  if (host && activity.value.source_type === "remote") {
    return `@${shortName}@${host}`;
  }
  return `@${shortName}`;
});

const actorUrl = computed(() => {
  const { shortName, host } = parseActor(activity.value.source_actor);
  if (host && activity.value.source_type === "remote") {
    return `https://${host}/@${shortName}`;
  }
  return `/@${shortName}`;
});

const actorDisplayName = computed(
  () =>
    activity.value.source_actor_display_name?.trim() || actorShortName.value,
);

const actorAvatar = computed(
  () => activity.value.source_actor_avatar_url || undefined,
);

const typeIcon = computed(() => TYPE_ICONS[activity.value.activity_type] ?? "");
const typeLabel = computed(() =>
  activity.value.activity_type === "create"
    ? ""
    : t(`activities.types.${activity.value.activity_type}`),
);

const visibilityIcon = computed(
  () => VISIBILITY_ICONS[activity.value.visibility] ?? "globe",
);
const visibilityLabel = computed(() =>
  t(`activities.visibility.${activity.value.visibility}`),
);

// ``content`` is sanitized HTML for local activities (produced by the
// server-side mention pipeline) and untrusted remote-supplied HTML otherwise.
// Both are reduced to safe segments: text, line breaks, and linkified
// mentions, hashtags, and URLs — remote HTML is never rendered verbatim.
const contentSegments = computed(() => {
  const raw = activity.value.content ?? activity.value.content_source ?? "";
  if (!raw) return [];
  return parseActivityContent(raw, {
    instanceDomain: instanceDomain.value,
    mentions: activity.value.mentions,
  });
});

// ``attachments`` carries the ActivityPub attachment documents of the
// activity's embedded object — ``Image``/image ``Document`` entries render
// inline, ``Audio`` entries get a player, and anything else becomes a link.
const attachments = computed(() => activity.value.attachments ?? []);
const imageAttachments = computed(() =>
  attachments.value.filter(
    (a) => a.url && (a.mediaType ?? "").startsWith("image/"),
  ),
);
const audioAttachments = computed(() =>
  attachments.value.filter(
    (a) =>
      a.url && (a.type === "Audio" || (a.mediaType ?? "").startsWith("audio/")),
  ),
);
const fileAttachments = computed(() =>
  attachments.value.filter(
    (a) =>
      a.url &&
      !imageAttachments.value.includes(a) &&
      !audioAttachments.value.includes(a),
  ),
);

// Inline font formatting survived from the source markup — rendered as
// classes rather than real tags so links/mentions keep their own elements.
const MARK_CLASSES: Record<ContentMark, string> = {
  bold: "activity-card__mark--bold",
  italic: "activity-card__mark--italic",
  strikethrough: "activity-card__mark--strikethrough",
  underline: "activity-card__mark--underline",
  code: "activity-card__mark--code",
};

function markClasses(segment: ContentSegment): string[] {
  return (segment.marks ?? []).map((mark) => MARK_CLASSES[mark]);
}

// ``like``/``announce`` cards are reaction wrappers: their object is a bare
// reference rendered through ``ActivityObjectEmbed``, they carry no
// editable content, and deleting one means retracting the reaction.
const isReaction = computed(
  () =>
    activity.value.activity_type === "like" ||
    activity.value.activity_type === "announce",
);
// Quote cards embed the quoted activity below their content — the
// ``in_reply_to_activity_id`` link doubles as the quote pointer.
const isQuote = computed(() => activity.value.activity_type === "quote");
// Only ``Create``-style types carry an editable object; reactions,
// tombstones, and relayed types have nothing to edit.
const isEditable = computed(() =>
  ["create", "reply", "quote"].includes(activity.value.activity_type),
);
const isOwner = computed(
  () =>
    authStore.user?.id != null &&
    authStore.user.id === activity.value.owner_user_id,
);
const canEdit = computed(
  () =>
    authStore.isAuthenticated &&
    isEditable.value &&
    (authStore.isAdmin || isOwner.value),
);
const canDelete = computed(
  () => authStore.isAuthenticated && (authStore.isAdmin || isOwner.value),
);
// The reacted-to activity once the embed resolves it — passed to the store
// so retracting the reaction updates its counters too.
const targetActivity = ref<ActivityResponse | null>(null);
const embedFailed = ref(false);
// ``can_interact`` is the server-computed gate: interaction types that make
// no sense to interact with (likes, tombstones) opt out; everything else is
// left to the backend's view checks, which stay authoritative.
const canInteract = computed(
  () => authStore.isAuthenticated && activity.value.can_interact !== false,
);
// The counters row renders for every viewer — anonymous ones included —
// whenever the activity type supports interaction. Only the mutating
// buttons are gated on authentication; the counters stay live since
// listing replies and like/boost actors are public reads.
const showInteractions = computed(
  () => !props.readonly && activity.value.can_interact !== false,
);
const interactionHint = computed(() =>
  authStore.isAuthenticated ? undefined : t("activities.loginToInteract"),
);
const liked = computed(
  () => activity.value.liked || store.isLiked(props.activity.id),
);
const liking = computed(() => store.isLiking(props.activity.id));
const boosted = computed(
  () => activity.value.boosted || store.isBoosted(props.activity.id),
);
const boosting = computed(() => store.isBoosting(props.activity.id));
const deleting = computed(() => store.isDeleting(props.activity.id));

// Cards backed by a stored activity row navigate to their
// ``/activities/{id}`` permalink — the whole card is the link target,
// Mastodon-style. Snapshot cards (notification payloads with no local
// row) carry no entity and stay inert.
const canNavigate = computed(
  () => !!activity.value.entity_type && !!activity.value.id,
);
const activityPageUrl = computed(() => `/activities/${activity.value.id}`);
// The object id stays the external link for snapshot cards only.
const objectLink = computed(
  () => activity.value.object_url || activity.value.source_id || null,
);
const copyTarget = computed(() =>
  canNavigate.value
    ? `${window.location.origin}${activityPageUrl.value}`
    : objectLink.value,
);

function onCardClick(event: MouseEvent) {
  if (!canNavigate.value) return;
  const target = event.target as HTMLElement | null;
  if (
    target?.closest(
      "a, button, input, textarea, select, label, audio, video, .activity-card__reply-composer, .activity-card__quote-composer",
    )
  ) {
    return;
  }
  if (window.getSelection()?.toString()) return;
  event.stopPropagation();
  void router?.push(activityPageUrl.value);
}

const actorsOpen = ref(false);
const actorsKind = ref<"likes" | "boosts">("likes");
const repliesOpen = ref(false);
const repliesLoaded = ref(false);
const repliesLoading = ref(false);
const localReplies = ref<ActivityResponse[]>([]);
const remoteReplies = ref<RemoteReply[]>([]);
const replyComposerOpen = ref(false);
const quotesOpen = ref(false);
const quotesLoaded = ref(false);
const quotesLoading = ref(false);
const localQuotes = ref<ActivityResponse[]>([]);
const remoteQuotes = ref<RemoteQuote[]>([]);
const quoteComposerOpen = ref(false);

onMounted(() => {
  if (props.expandReplies && canNavigate.value) {
    repliesOpen.value = true;
    void loadReplies();
  }
});

async function toggleLike() {
  const undoing = liked.value;
  try {
    if (undoing) await store.unlike(activity.value);
    else await store.like(activity.value);
  } catch (err) {
    toast.push({
      type: "error",
      message:
        getApiErrorMessage(err) ||
        t(undoing ? "activities.unlikeError" : "activities.likeError"),
    });
  }
}

async function toggleBoost() {
  const undoing = boosted.value;
  try {
    if (undoing) await store.unboost(activity.value);
    else await store.boost(activity.value);
  } catch (err) {
    toast.push({
      type: "error",
      message:
        getApiErrorMessage(err) ||
        t(undoing ? "activities.unboostError" : "activities.boostError"),
    });
  }
}

function openActors(kind: "likes" | "boosts") {
  actorsKind.value = kind;
  actorsOpen.value = true;
}

async function loadReplies() {
  repliesLoading.value = true;
  try {
    const response = await listActivityReplies(props.activity.id);
    localReplies.value = response.activities;
    remoteReplies.value = response.remote_replies;
    repliesLoaded.value = true;
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("activities.repliesError"),
    });
  } finally {
    repliesLoading.value = false;
  }
}

async function toggleReplies() {
  repliesOpen.value = !repliesOpen.value;
  if (repliesOpen.value && !repliesLoaded.value) await loadReplies();
}

async function loadQuotes() {
  quotesLoading.value = true;
  try {
    const response = await listActivityQuotes(props.activity.id);
    localQuotes.value = response.activities;
    remoteQuotes.value = response.remote_quotes;
    quotesLoaded.value = true;
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("activities.quotesError"),
    });
  } finally {
    quotesLoading.value = false;
  }
}

async function toggleQuotes() {
  quotesOpen.value = !quotesOpen.value;
  if (quotesOpen.value && !quotesLoaded.value) await loadQuotes();
}

type QuoteEntry =
  | {
      kind: "local";
      key: string;
      publishedAt: string;
      activity: ActivityResponse;
    }
  | { kind: "remote"; key: string; publishedAt: string; quote: RemoteQuote };

// Quotes are not threaded — unlike replies they attach to the quoted post
// itself, so the listing is a flat, chronologically ordered merge of local
// cards and federated interaction records.
const quoteEntries = computed<QuoteEntry[]>(() =>
  [
    ...localQuotes.value.map((a) => ({
      kind: "local" as const,
      key: a.id,
      publishedAt: a.published_at,
      activity: a,
    })),
    ...remoteQuotes.value.map((q) => ({
      kind: "remote" as const,
      key: q.id,
      publishedAt: q.published_at ?? "",
      quote: q,
    })),
  ].sort((a, b) => a.publishedAt.localeCompare(b.publishedAt)),
);

type ReplyEntry =
  | {
      kind: "local";
      key: string;
      publishedAt: string;
      activity: ActivityResponse;
    }
  | { kind: "remote"; key: string; publishedAt: string; reply: RemoteReply };

interface ReplyThread {
  /** Key of the thread's head — the entry replying directly to this card. */
  key: string;
  entries: ReplyEntry[];
}

// The replies listing returns the whole sub-thread (replies to replies
// included). Mastodon-style single threading: every descendant is unfolded
// flat into the thread rooted at its ``root reply`` ancestor — the entry
// replying directly to this card — and each thread gets its own vertical
// line. Entries whose parent is missing from the listing (visibility-
// filtered or deleted) become heads of their own thread.
const replyThreads = computed<ReplyThread[]>(() => {
  const entries: ReplyEntry[] = [
    ...localReplies.value.map((a) => ({
      kind: "local" as const,
      key: a.id,
      publishedAt: a.published_at,
      activity: a,
    })),
    ...remoteReplies.value.map((r) => ({
      kind: "remote" as const,
      key: r.id,
      publishedAt: r.published_at ?? "",
      reply: r,
    })),
  ];

  // Every key a child may use to reference a node: local replies point at
  // the parent's activity id, remote replies at its object id.
  const nodeKeys = (entry: ReplyEntry): (string | null | undefined)[] =>
    entry.kind === "local"
      ? [entry.activity.id, entry.activity.source_id, entry.activity.object_url]
      : [entry.reply.id, entry.reply.object_id, entry.reply.url];
  const parentKey = (entry: ReplyEntry): string | null | undefined =>
    entry.kind === "local"
      ? entry.activity.in_reply_to_activity_id
      : entry.reply.in_reply_to;

  const byKey = new Map<string, ReplyEntry>();
  for (const entry of entries) {
    for (const key of nodeKeys(entry)) {
      if (key && !byKey.has(key)) byKey.set(key, entry);
    }
  }
  const rootKeys = new Set(
    [
      props.activity.id,
      activity.value.source_id,
      activity.value.object_url,
    ].filter((key): key is string => !!key),
  );

  const headOf = (entry: ReplyEntry): ReplyEntry => {
    let current = entry;
    const seen = new Set<string>([current.key]);
    for (;;) {
      const parent = parentKey(current);
      if (!parent || rootKeys.has(parent)) return current;
      const next = byKey.get(parent);
      if (!next || seen.has(next.key)) return current;
      seen.add(next.key);
      current = next;
    }
  };

  const threads = new Map<string, ReplyThread>();
  for (const entry of entries) {
    const head = headOf(entry);
    let thread = threads.get(head.key);
    if (!thread) {
      thread = { key: head.key, entries: [] };
      threads.set(head.key, thread);
    }
    thread.entries.push(entry);
  }

  const ordered = [...threads.values()];
  for (const thread of ordered) {
    // The head opens the thread; the rest unfolds chronologically.
    thread.entries.sort((a, b) =>
      a.key === thread.key
        ? -1
        : b.key === thread.key
          ? 1
          : a.publishedAt.localeCompare(b.publishedAt),
    );
  }
  ordered.sort((a, b) =>
    (a.entries[0]?.publishedAt ?? "").localeCompare(
      b.entries[0]?.publishedAt ?? "",
    ),
  );
  return ordered;
});

// Mirrors ``_MENTION_HANDLE_RE`` in songhive/models/activity.py — only
// handle-shaped mention names are taggable in the reply prefill.
const MENTION_HANDLE_RE = /^@?[a-zA-Z0-9_.-]+(@[a-zA-Z0-9.-]+)?$/;

// Mastodon-style reply prefill: the replied-to author first, then every
// actor the activity already mentions — deduplicated (handles compare
// case-insensitively, actor URLs catch the same actor behind a different
// handle spelling) and skipping the replying user, who never tags
// themselves.
const replyInitialStatus = computed(() => {
  const self = authStore.user;
  const selfName = self?.username?.toLowerCase();
  const selfHandle = selfName ? `@${selfName}` : null;
  const seenHandles = new Set<string>();
  const seenActors = new Set<string>();
  const handles: string[] = [];

  const push = (
    handle: string,
    actorUrl?: string | null,
    userId?: string | null,
  ) => {
    if (!MENTION_HANDLE_RE.test(handle)) return;
    const normalized = handle.startsWith("@") ? handle : `@${handle}`;
    const key = normalized.toLowerCase();
    if (
      seenHandles.has(key) ||
      (actorUrl != null && seenActors.has(actorUrl)) ||
      (self?.id != null && userId === self.id) ||
      key === selfHandle
    ) {
      return;
    }
    seenHandles.add(key);
    if (actorUrl) seenActors.add(actorUrl);
    handles.push(normalized);
  };

  const author = parseActorRef(
    activity.value.source_actor,
    instanceDomain.value,
  );
  if (!isOwner.value && author.username?.toLowerCase() !== selfName) {
    push(author.handle, activity.value.source_actor);
  }
  for (const mention of activity.value.mentions ?? []) {
    push(mention.handle, mention.actor_url, mention.user_id);
  }
  return handles.length ? `${handles.join(" ")} ` : "";
});

async function submitReply(payload: StatusComposerPayload) {
  const created = await store.reply(activity.value, {
    status: payload.status,
    content_type: payload.content_type,
    visibility: payload.visibility,
    language: payload.language,
    media_ids: payload.media_ids,
    track_ids: payload.track_ids,
  });
  if (repliesLoaded.value) {
    localReplies.value = [...localReplies.value, created];
  } else {
    await loadReplies();
  }
  repliesOpen.value = true;
}

async function submitQuote(payload: StatusComposerPayload) {
  const created = await store.quote(activity.value, {
    status: payload.status,
    content_type: payload.content_type,
    visibility: payload.visibility,
    language: payload.language,
    media_ids: payload.media_ids,
    track_ids: payload.track_ids,
  });
  if (quotesLoaded.value) {
    localQuotes.value = [...localQuotes.value, created];
  } else {
    await loadQuotes();
  }
  quotesOpen.value = true;
}

async function remove() {
  // Deleting one's own reaction means retracting it on the target (an
  // ``Undo`` federates); admins retracting someone else's reaction go
  // through the regular activity deletion path.
  const retracting =
    isReaction.value &&
    isOwner.value &&
    !!activity.value.in_reply_to_activity_id;
  const confirmed = await confirmStore.open({
    title: t("activities.delete.title"),
    message: t(
      retracting
        ? activity.value.activity_type === "like"
          ? "activities.unlikeConfirm"
          : "activities.unboostConfirm"
        : "activities.delete.confirm",
    ),
    confirmLabel: t("common.delete"),
    danger: true,
  });
  if (!confirmed) return;
  try {
    if (retracting) {
      await store.retractReaction(activity.value, targetActivity.value);
    } else {
      await store.remove(props.activity.id);
    }
    toast.push({ type: "success", message: t("activities.delete.done") });
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("activities.delete.error"),
    });
  }
}

async function copyUrl() {
  if (!copyTarget.value) return;
  try {
    await navigator.clipboard.writeText(copyTarget.value);
    toast.push({ type: "success", message: t("activities.copyUrl.done") });
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("activities.copyUrl.error"),
    });
  }
}
</script>

<template>
  <article
    v-if="!removed"
    class="activity-card"
    :class="{ 'activity-card--link': canNavigate }"
    @click="onCardClick"
  >
    <header class="activity-card__header">
      <AppAvatar :src="actorAvatar" :name="actorDisplayName" size="sm" />
      <div class="activity-card__meta">
        <a
          v-if="activity.source_type === 'remote'"
          :href="actorUrl"
          class="activity-card__actor"
          target="_blank"
        >
          <span class="activity-card__display-name">{{
            actorDisplayName
          }}</span>
          <span class="activity-card__handle">{{ actorName }}</span>
        </a>
        <RouterLink
          v-else
          :to="'/@' + actorShortName"
          class="activity-card__actor"
        >
          <span class="activity-card__display-name">{{
            actorDisplayName
          }}</span>
          <span class="activity-card__handle">{{ actorName }}</span>
        </RouterLink>
        <RouterLink
          v-if="canNavigate"
          :to="activityPageUrl"
          class="activity-card__time"
          >{{ formatDateTime(activity.published_at) }}</RouterLink
        >
        <a
          v-else-if="objectLink"
          :href="objectLink"
          target="_blank"
          rel="noopener"
          class="activity-card__time"
          >{{ formatDateTime(activity.published_at) }}</a
        >
        <span v-else class="activity-card__time">{{
          formatDateTime(activity.published_at)
        }}</span>
      </div>
      <div class="activity-card__badges">
        <span v-if="typeLabel" class="activity-card__type" :title="typeLabel">
          <AppIcon v-if="typeIcon" :name="typeIcon" spacing="right" />{{
            typeLabel
          }}
        </span>
        <AppIcon
          :name="visibilityIcon"
          :title="visibilityLabel"
          :aria-label="visibilityLabel"
        />
      </div>
    </header>

    <div v-if="isReaction" class="activity-card__object">
      <ActivityObjectEmbed
        v-if="activity.in_reply_to_activity_id && !embedFailed"
        :activity-id="activity.in_reply_to_activity_id"
        @loaded="targetActivity = $event"
        @error="embedFailed = true"
      />
      <p v-else class="activity-card__object-unavailable">
        {{ t("activities.objectUnavailable") }}
      </p>
    </div>

    <p
      v-if="contentSegments.length"
      class="activity-card__content"
      :lang="activity.language || undefined"
    >
      <template v-for="(segment, index) in contentSegments" :key="index">
        <template v-if="segment.type === 'text'">
          <span v-if="segment.marks" :class="markClasses(segment)">{{
            segment.value
          }}</span>
          <template v-else>{{ segment.value }}</template>
        </template>
        <RouterLink
          v-else-if="segment.type === 'mention' && segment.username"
          :to="{ name: 'userProfile', params: { username: segment.username } }"
          :class="['activity-card__mention', ...markClasses(segment)]"
          >{{ segment.handle }}</RouterLink
        >
        <a
          v-else-if="segment.type === 'mention'"
          :href="segment.url"
          target="_blank"
          rel="noopener"
          :class="['activity-card__mention', ...markClasses(segment)]"
          >{{ segment.handle }}</a
        >
        <RouterLink
          v-else-if="segment.type === 'tag'"
          :to="{ name: 'tag', params: { name: segment.name } }"
          :class="markClasses(segment)"
          >{{ segment.display }}</RouterLink
        >
        <RouterLink
          v-else-if="segment.to"
          :to="segment.to"
          :class="markClasses(segment)"
          >{{ segment.label }}</RouterLink
        >
        <a
          v-else
          :href="segment.url"
          target="_blank"
          rel="noopener"
          :class="markClasses(segment)"
          >{{ segment.label }}</a
        >
      </template>
    </p>

    <div v-if="attachments.length" class="activity-card__attachments">
      <a
        v-for="(attachment, index) in imageAttachments"
        :key="`image-${index}`"
        :href="attachment.url"
        target="_blank"
        rel="noopener"
        class="activity-card__attachment-image-link"
      >
        <img
          :src="attachment.url"
          :alt="attachment.name ?? ''"
          class="activity-card__attachment-image"
          loading="lazy"
        />
      </a>
      <div
        v-for="(attachment, index) in audioAttachments"
        :key="`audio-${index}`"
        class="activity-card__attachment-audio"
      >
        <span v-if="attachment.name" class="activity-card__attachment-name">
          <AppIcon name="music" spacing="right" />{{ attachment.name }}
        </span>
        <audio
          :src="attachment.url"
          controls
          preload="none"
          class="activity-card__attachment-player"
        />
      </div>
      <ul v-if="fileAttachments.length" class="activity-card__attachment-list">
        <li
          v-for="(attachment, index) in fileAttachments"
          :key="`file-${index}`"
        >
          <AppIcon name="paperclip" spacing="right" />
          <a :href="attachment.url" target="_blank" rel="noopener">{{
            attachment.name || attachment.url
          }}</a>
        </li>
      </ul>
    </div>

    <div
      v-if="isQuote && activity.in_reply_to_activity_id"
      class="activity-card__quote"
    >
      <ActivityObjectEmbed :activity-id="activity.in_reply_to_activity_id" />
    </div>

    <footer
      v-if="copyTarget || showInteractions || (!props.readonly && canDelete)"
      class="activity-card__actions"
    >
      <template v-if="showInteractions">
        <span class="activity-card__action">
          <AppButton
            variant="ghost"
            size="sm"
            icon="reply"
            :disabled="!canInteract"
            :title="interactionHint ?? t('activities.reply')"
            :aria-label="t('activities.reply')"
            @click="replyComposerOpen = !replyComposerOpen"
          />
          <button
            type="button"
            class="activity-card__count"
            :title="t('activities.replies')"
            :aria-label="t('activities.replies')"
            :aria-expanded="repliesOpen"
            @click="toggleReplies"
          >
            {{ activity.reply_count }}
          </button>
        </span>
        <span class="activity-card__action">
          <AppButton
            variant="ghost"
            size="sm"
            icon="quote-left"
            :disabled="!canInteract"
            :title="interactionHint ?? t('activities.quote')"
            :aria-label="t('activities.quote')"
            @click="quoteComposerOpen = !quoteComposerOpen"
          />
          <button
            type="button"
            class="activity-card__count"
            :title="t('activities.quotes')"
            :aria-label="t('activities.quotes')"
            :aria-expanded="quotesOpen"
            @click="toggleQuotes"
          >
            {{ activity.quote_count }}
          </button>
        </span>
        <span class="activity-card__action">
          <AppButton
            variant="ghost"
            size="sm"
            icon="retweet"
            :class="{ 'activity-card__action-btn--active': boosted }"
            :loading="boosting"
            :disabled="!canInteract"
            :title="
              interactionHint ??
              (boosted ? t('activities.unboost') : t('activities.boost'))
            "
            :aria-label="
              boosted ? t('activities.unboost') : t('activities.boost')
            "
            :aria-pressed="boosted"
            @click="toggleBoost"
          />
          <button
            type="button"
            class="activity-card__count"
            :title="t('activities.actors.boosts')"
            :aria-label="t('activities.actors.boosts')"
            @click="openActors('boosts')"
          >
            {{ activity.boost_count }}
          </button>
        </span>
        <span class="activity-card__action">
          <AppButton
            variant="ghost"
            size="sm"
            icon="heart"
            :icon-variant="liked ? 'solid' : 'regular'"
            :class="{ 'activity-card__action-btn--active': liked }"
            :loading="liking"
            :disabled="!canInteract"
            :title="
              interactionHint ??
              (liked ? t('activities.unlike') : t('activities.like'))
            "
            :aria-label="liked ? t('activities.unlike') : t('activities.like')"
            :aria-pressed="liked"
            @click="toggleLike"
          />
          <button
            type="button"
            class="activity-card__count"
            :title="t('activities.actors.likes')"
            :aria-label="t('activities.actors.likes')"
            @click="openActors('likes')"
          >
            {{ activity.like_count }}
          </button>
        </span>
      </template>
      <span class="activity-card__actions-spacer" />
      <AppButton
        v-if="!props.readonly && canEdit"
        variant="ghost"
        size="sm"
        icon="pen-to-square"
        :title="t('common.edit')"
        :aria-label="t('common.edit')"
        @click="editOpen = true"
      />
      <AppButton
        v-if="!props.readonly && canDelete"
        variant="ghost"
        size="sm"
        icon="trash"
        :loading="deleting"
        :title="t('common.delete')"
        :aria-label="t('common.delete')"
        @click="remove"
      />
      <AppButton
        v-if="copyTarget"
        variant="ghost"
        size="sm"
        icon="clipboard"
        :title="t('browse.share.copyUrl')"
        :aria-label="t('browse.share.copyUrl')"
        @click="copyUrl"
      />
    </footer>

    <div v-if="replyComposerOpen" class="activity-card__reply-composer">
      <StatusComposer
        :submit="submitReply"
        :submit-label="t('activities.replySubmit')"
        :placeholder="t('activities.replyPlaceholder')"
        :initial-status="replyInitialStatus"
        :initial-visibility="activity.visibility"
        @submitted="replyComposerOpen = false"
      />
    </div>

    <div v-if="repliesOpen" class="activity-card__replies">
      <div v-if="repliesLoading" class="activity-card__replies-loading">
        <AppSpinner />
      </div>
      <p
        v-else-if="repliesLoaded && !replyThreads.length"
        class="activity-card__replies-empty"
      >
        {{ t("activities.noReplies") }}
      </p>
      <div
        v-for="thread in replyThreads"
        :key="thread.key"
        class="activity-card__thread"
      >
        <template v-for="entry in thread.entries" :key="entry.key">
          <ActivityCard
            v-if="entry.kind === 'local'"
            :activity="entry.activity"
            class="activity-card__reply"
          />
          <ActivityRemoteReply
            v-else
            :reply="entry.reply"
            class="activity-card__reply"
          />
        </template>
      </div>
    </div>

    <div v-if="quoteComposerOpen" class="activity-card__quote-composer">
      <StatusComposer
        :submit="submitQuote"
        :submit-label="t('activities.quoteSubmit')"
        :placeholder="t('activities.quotePlaceholder')"
        :initial-visibility="activity.visibility"
        @submitted="quoteComposerOpen = false"
      />
    </div>

    <div v-if="quotesOpen" class="activity-card__quotes">
      <div v-if="quotesLoading" class="activity-card__replies-loading">
        <AppSpinner />
      </div>
      <p
        v-else-if="quotesLoaded && !quoteEntries.length"
        class="activity-card__replies-empty"
      >
        {{ t("activities.noQuotes") }}
      </p>
      <template v-for="entry in quoteEntries" :key="entry.key">
        <ActivityCard
          v-if="entry.kind === 'local'"
          :activity="entry.activity"
          class="activity-card__reply"
        />
        <ActivityRemoteReply
          v-else
          :reply="entry.quote"
          class="activity-card__reply"
        />
      </template>
    </div>

    <ActivityActorsModal
      v-if="!props.readonly"
      :open="actorsOpen"
      :activity-id="activity.id"
      :kind="actorsKind"
      @close="actorsOpen = false"
    />

    <ActivityEditModal
      v-if="!props.readonly"
      :open="editOpen"
      :activity="activity"
      @close="editOpen = false"
    />
  </article>
</template>

<style scoped>
.activity-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

@media (min-width: 75rem) {
  .activity-card {
    width: calc(70rem - var(--sidebar-width));
  }
}

.activity-card--link {
  cursor: pointer;
}

.activity-card__header {
  display: flex;
  gap: var(--space-3);
}

.activity-card__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.activity-card__actor {
  display: flex;
  flex-wrap: wrap;
  flex-direction: column;
  align-items: baseline;
  word-break: break-all;
  text-decoration: none;
}

.activity-card__time {
  text-decoration: none;
}

.activity-card__actor:hover,
.activity-card__time:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

.activity-card__display-name {
  font-weight: 600;
  color: var(--color-text);
}

.activity-card__handle {
  color: var(--color-text-muted);
  font-size: 0.9em;
}

.activity-card__time {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.activity-card__badges {
  display: inline-flex;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.activity-card__type {
  display: inline-flex;
  white-space: nowrap;
}

.activity-card__content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.activity-card__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.activity-card__action {
  display: inline-flex;
  align-items: center;
}

.activity-card__action-btn--active {
  color: var(--color-accent);
}

.activity-card__count {
  padding: var(--space-1) var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--color-text-muted);
  font-size: 0.875rem;
  cursor: pointer;
}

.activity-card__count:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}

.activity-card__actions-spacer {
  flex: 1;
}

.activity-card__reply-composer,
.activity-card__quote-composer {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.activity-card__quote :deep(.activity-card) {
  width: auto;
}

.activity-card__replies,
.activity-card__quotes {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.activity-card__thread {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-left: var(--space-4);
  border-left: 2px solid var(--color-border);
}

.activity-card__replies :deep(.activity-card) {
  width: auto;
}

.activity-card__object :deep(.activity-card) {
  width: auto;
}

.activity-card__object-unavailable {
  margin: 0;
  color: var(--color-text-muted);
}

.activity-card__replies-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-3);
}

.activity-card__replies-empty {
  margin: 0;
  color: var(--color-text-muted);
}

.activity-card__content a {
  color: var(--color-text-link);
}

.activity-card__attachments {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.activity-card__attachment-image {
  max-width: 100%;
  max-height: 24rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  object-fit: contain;
}

.activity-card__attachment-audio {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.activity-card__attachment-name {
  font-size: 0.9rem;
  color: var(--color-text-muted);
}

.activity-card__attachment-player {
  width: 100%;
  max-width: 30rem;
}

.activity-card__attachment-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.activity-card__attachment-list a {
  color: var(--color-text-link);
  word-break: break-all;
}

.activity-card__mention {
  font-weight: 500;
}

.activity-card__mark--bold {
  font-weight: 700;
}

.activity-card__mark--italic {
  font-style: italic;
}

.activity-card__mark--strikethrough {
  text-decoration: line-through;
}

.activity-card__mark--underline {
  text-decoration: underline;
}

.activity-card__mark--strikethrough.activity-card__mark--underline {
  text-decoration: underline line-through;
}

.activity-card__mark--code {
  font-family: ui-monospace, monospace;
  font-size: 0.9em;
  padding: 0 0.2em;
  border-radius: var(--radius-sm);
  background-color: var(--color-surface-secondary);
}

a:hover {
  color: var(--color-text-hover);
}
</style>
