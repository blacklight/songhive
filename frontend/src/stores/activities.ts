import { computed, ref, type Ref } from "vue";
import { defineStore } from "pinia";
import {
  deleteActivity as deleteActivityApi,
  listEntityActivities,
  likeActivity as likeActivityApi,
  updateActivity as updateActivityApi,
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
  const deletingIds: Ref<Set<string>> = ref(new Set());
  // Latest PATCH result per activity id, so cards rendered from lists that
  // are not backed by ``items`` (profile tabs, tag detail, notifications)
  // still reflect edits immediately.
  const updatedById: Ref<Map<string, ActivityResponse>> = ref(new Map());
  const removedIds: Ref<Set<string>> = ref(new Set());

  const isLiked = computed(() => (id: string) => likedIds.value.has(id));
  const isLiking = computed(() => (id: string) => likingIds.value.has(id));
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

  async function like(activity: ActivityResponse): Promise<void> {
    if (likedIds.value.has(activity.id) || likingIds.value.has(activity.id))
      return;
    likingIds.value.add(activity.id);
    try {
      await likeActivityApi(activity.id);
      likedIds.value.add(activity.id);
    } finally {
      likingIds.value.delete(activity.id);
    }
  }

  async function remove(activityId: string): Promise<void> {
    if (deletingIds.value.has(activityId)) return;
    deletingIds.value.add(activityId);
    try {
      await deleteActivityApi(activityId);
      const index = items.value.findIndex((a) => a.id === activityId);
      if (index !== -1) items.value.splice(index, 1);
      likedIds.value.delete(activityId);
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
    isDeleting,
    updatedActivity,
    isRemoved,
    load,
    setFilter,
    loadMore,
    like,
    remove,
    update,
    $resetFeed,
  };
});
