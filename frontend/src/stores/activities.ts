import { computed, ref, type Ref } from "vue";
import { defineStore } from "pinia";
import {
  boostActivity as boostActivityApi,
  deleteActivity as deleteActivityApi,
  listEntityActivities,
  likeActivity as likeActivityApi,
  quoteActivity as quoteActivityApi,
  replyToActivity as replyToActivityApi,
  unboostActivity as unboostActivityApi,
  unlikeActivity as unlikeActivityApi,
  updateActivity as updateActivityApi,
  type ActivityReplyRequest,
  type ActivityResponse,
  type ActivityUpdate,
  type ListActivitiesParams,
} from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";

export type ActivityFeedFilter =
  "all" | "local" | "remote" | "replies" | "boosts" | "likes";

const FILTER_PARAMS: Record<
  ActivityFeedFilter,
  Pick<ListActivitiesParams, "activity_type" | "source_type">
> = {
  all: {},
  local: { source_type: "local" },
  remote: { source_type: "remote" },
  replies: { activity_type: "reply" },
  boosts: { activity_type: "announce" },
  likes: { activity_type: "like" },
};

export const useActivitiesStore = defineStore("activities", () => {
  const items: Ref<ActivityResponse[]> = ref([]);
  const entityType = ref("");
  const entityId = ref("");
  const filter: Ref<ActivityFeedFilter> = ref("all");
  const nextCursor: Ref<string | null> = ref(null);
  const hasMore = ref(false);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error: Ref<string | null> = ref(null);
  const likedIds: Ref<Set<string>> = ref(new Set());
  const likingIds: Ref<Set<string>> = ref(new Set());
  const boostedIds: Ref<Set<string>> = ref(new Set());
  const boostingIds: Ref<Set<string>> = ref(new Set());
  const deletingIds: Ref<Set<string>> = ref(new Set());
  // Latest PATCH result per activity id, so cards rendered from lists that
  // are not backed by ``items`` (profile tabs, tag detail, notifications)
  // still reflect edits immediately.
  const updatedById: Ref<Map<string, ActivityResponse>> = ref(new Map());
  const removedIds: Ref<Set<string>> = ref(new Set());

  const isLiked = computed(() => (id: string) => likedIds.value.has(id));
  const isLiking = computed(() => (id: string) => likingIds.value.has(id));
  const isBoosted = computed(() => (id: string) => boostedIds.value.has(id));
  const isBoosting = computed(() => (id: string) => boostingIds.value.has(id));
  const isDeleting = computed(() => (id: string) => deletingIds.value.has(id));
  const updatedActivity = computed(
    () => (id: string) => updatedById.value.get(id),
  );
  const isRemoved = computed(() => (id: string) => removedIds.value.has(id));

  async function fetchPage(
    cursor: string | null,
    append: boolean,
  ): Promise<void> {
    const page = await listEntityActivities(entityType.value, entityId.value, {
      ...FILTER_PARAMS[filter.value],
      cursor: cursor ?? undefined,
    });
    items.value = append
      ? [...items.value, ...page.activities]
      : page.activities;
    nextCursor.value = page.next_cursor ?? null;
    hasMore.value = !!page.next_cursor;
  }

  async function load(
    type: string,
    id: string,
    nextFilter?: ActivityFeedFilter,
  ): Promise<void> {
    if (loading.value) return;
    entityType.value = type;
    entityId.value = id;
    filter.value = nextFilter ?? filter.value;
    loading.value = true;
    error.value = null;
    try {
      await fetchPage(null, false);
    } catch (err) {
      error.value =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : "Unknown error");
      items.value = [];
      hasMore.value = false;
      nextCursor.value = null;
    } finally {
      loading.value = false;
    }
  }

  async function setFilter(next: ActivityFeedFilter): Promise<void> {
    if (next === filter.value) return;
    await load(entityType.value, entityId.value, next);
  }

  async function loadMore(): Promise<void> {
    if (loadingMore.value || !hasMore.value || !nextCursor.value) return;
    loadingMore.value = true;
    error.value = null;
    try {
      await fetchPage(nextCursor.value, true);
    } catch (err) {
      error.value =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : "Unknown error");
      hasMore.value = false;
    } finally {
      loadingMore.value = false;
    }
  }

  /**
   * Merge interaction fields (counters and liked/boosted flags) into every
   * cached copy of ``activity`` — ``items``, ``updatedById``, or the card's
   * own copy seeded into ``updatedById`` when the activity is not tracked —
   * so cards update immediately wherever they render from.
   */
  function patchInteraction(
    activity: ActivityResponse,
    patch: Partial<
      Pick<
        ActivityResponse,
        | "like_count"
        | "boost_count"
        | "reply_count"
        | "quote_count"
        | "liked"
        | "boosted"
      >
    >,
  ): void {
    const base =
      updatedById.value.get(activity.id) ??
      items.value.find((a) => a.id === activity.id) ??
      activity;
    const merged = { ...base, ...patch };
    updatedById.value.set(activity.id, merged);
    const index = items.value.findIndex((a) => a.id === activity.id);
    if (index !== -1) items.value.splice(index, 1, merged);
  }

  async function like(activity: ActivityResponse): Promise<void> {
    if (likedIds.value.has(activity.id) || likingIds.value.has(activity.id))
      return;
    likingIds.value.add(activity.id);
    try {
      await likeActivityApi(activity.id);
      likedIds.value.add(activity.id);
      const current = updatedById.value.get(activity.id) ?? activity;
      patchInteraction(current, {
        liked: true,
        like_count: current.like_count + (current.liked ? 0 : 1),
      });
    } finally {
      likingIds.value.delete(activity.id);
    }
  }

  async function unlike(activity: ActivityResponse): Promise<void> {
    if (likingIds.value.has(activity.id)) return;
    likingIds.value.add(activity.id);
    try {
      await unlikeActivityApi(activity.id);
      const current = updatedById.value.get(activity.id) ?? activity;
      const wasLiked = current.liked || likedIds.value.has(activity.id);
      likedIds.value.delete(activity.id);
      patchInteraction(current, {
        liked: false,
        like_count: Math.max(0, current.like_count - (wasLiked ? 1 : 0)),
      });
    } finally {
      likingIds.value.delete(activity.id);
    }
  }

  async function boost(activity: ActivityResponse): Promise<void> {
    if (boostedIds.value.has(activity.id) || boostingIds.value.has(activity.id))
      return;
    boostingIds.value.add(activity.id);
    try {
      await boostActivityApi(activity.id);
      boostedIds.value.add(activity.id);
      const current = updatedById.value.get(activity.id) ?? activity;
      patchInteraction(current, {
        boosted: true,
        boost_count: current.boost_count + (current.boosted ? 0 : 1),
      });
    } finally {
      boostingIds.value.delete(activity.id);
    }
  }

  async function unboost(activity: ActivityResponse): Promise<void> {
    if (boostingIds.value.has(activity.id)) return;
    boostingIds.value.add(activity.id);
    try {
      await unboostActivityApi(activity.id);
      const current = updatedById.value.get(activity.id) ?? activity;
      const wasBoosted = current.boosted || boostedIds.value.has(activity.id);
      boostedIds.value.delete(activity.id);
      patchInteraction(current, {
        boosted: false,
        boost_count: Math.max(0, current.boost_count - (wasBoosted ? 1 : 0)),
      });
    } finally {
      boostingIds.value.delete(activity.id);
    }
  }

  /**
   * Retract the user's own ``like``/``announce`` reaction activity.
   *
   * The reaction row references the reacted activity through
   * ``in_reply_to_activity_id`` — the unretraction endpoints live on the
   * target, so ``target`` (when the card's embed already fetched it) goes
   * through ``unlike``/``unboost`` to keep its counters in sync; otherwise
   * the bare API call is enough and the reaction card is dropped.
   */
  async function retractReaction(
    reaction: ActivityResponse,
    target?: ActivityResponse | null,
  ): Promise<void> {
    const targetId = reaction.in_reply_to_activity_id;
    if (!targetId || deletingIds.value.has(reaction.id)) return;
    deletingIds.value.add(reaction.id);
    try {
      if (reaction.activity_type === "like") {
        if (target) await unlike(target);
        else await unlikeActivityApi(targetId);
      } else {
        if (target) await unboost(target);
        else await unboostActivityApi(targetId);
      }
      const index = items.value.findIndex((a) => a.id === reaction.id);
      if (index !== -1) items.value.splice(index, 1);
      updatedById.value.delete(reaction.id);
      removedIds.value.add(reaction.id);
    } finally {
      deletingIds.value.delete(reaction.id);
    }
  }

  async function reply(
    activity: ActivityResponse,
    body: ActivityReplyRequest,
  ): Promise<ActivityResponse> {
    const created = await replyToActivityApi(activity.id, body);
    const current = updatedById.value.get(activity.id) ?? activity;
    patchInteraction(current, { reply_count: current.reply_count + 1 });
    return created;
  }

  async function quote(
    activity: ActivityResponse,
    body: ActivityReplyRequest,
  ): Promise<ActivityResponse> {
    const created = await quoteActivityApi(activity.id, body);
    const current = updatedById.value.get(activity.id) ?? activity;
    patchInteraction(current, { quote_count: current.quote_count + 1 });
    return created;
  }

  async function remove(activityId: string): Promise<void> {
    if (deletingIds.value.has(activityId)) return;
    deletingIds.value.add(activityId);
    try {
      await deleteActivityApi(activityId);
      const index = items.value.findIndex((a) => a.id === activityId);
      if (index !== -1) items.value.splice(index, 1);
      likedIds.value.delete(activityId);
      boostedIds.value.delete(activityId);
      updatedById.value.delete(activityId);
      removedIds.value.add(activityId);
    } finally {
      deletingIds.value.delete(activityId);
    }
  }

  async function update(
    activityId: string,
    body: ActivityUpdate,
  ): Promise<void> {
    const updated = await updateActivityApi(activityId, body);
    updatedById.value.set(activityId, updated);
    const index = items.value.findIndex((a) => a.id === activityId);
    if (index !== -1) items.value.splice(index, 1, updated);
  }

  function $resetFeed(): void {
    items.value = [];
    entityType.value = "";
    entityId.value = "";
    filter.value = "all";
    nextCursor.value = null;
    hasMore.value = false;
    error.value = null;
  }

  return {
    items,
    entityType,
    entityId,
    filter,
    nextCursor,
    hasMore,
    loading,
    loadingMore,
    error,
    likedIds,
    isLiked,
    isLiking,
    boostedIds,
    isBoosted,
    isBoosting,
    isDeleting,
    updatedActivity,
    isRemoved,
    load,
    setFilter,
    loadMore,
    like,
    unlike,
    boost,
    unboost,
    retractReaction,
    reply,
    quote,
    remove,
    update,
    $resetFeed,
  };
});
