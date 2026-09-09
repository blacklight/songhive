<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { getApiErrorMessage } from "@/api/client";
import { listTracksWithMeta } from "@/api/tracks";
import type { TrackResponse } from "@/api/tracks";
import type { ActivityListResponse, ActivityResponse } from "@/api/activities";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import AppTabs from "@/components/ui/AppTabs.vue";
import SortControl from "@/components/ui/SortControl.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import TaggedItemCard from "@/components/tags/TaggedItemCard.vue";
import TrackList from "@/components/library/TrackList.vue";

export interface Item {
  type: string;
  id: string;
}

export interface ListParams {
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  type?: string;
}

export interface ListResult {
  items: Item[];
  total: number;
  offset: number;
}

export type ItemKind = "tag" | "genre";

export interface ActivityListParams {
  limit?: number;
  cursor?: string;
}

export interface Props {
  kind: ItemKind;
  name: string;
  availableTypes: string[];
  loadItems: (name: string, params: ListParams) => Promise<ListResult>;
  deleteItem: (name: string) => Promise<unknown>;
  activityLoader?: (
    name: string,
    params: ActivityListParams,
  ) => Promise<ActivityListResponse>;
}

const props = defineProps<Props>();

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const confirm = useConfirmStore();
const toast = useToastStore();

const LIMIT = 24;

const items = ref<Item[]>([]);
const tracks = ref<TrackResponse[]>([]);
const total = ref(0);
const offset = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const deleting = ref(false);

const sortBy = ref<string>("created_at");
const sortDir = ref<"asc" | "desc">("desc");
const activeType = ref<string>("");
const visibleTypes = ref<string[]>([]);

const activities = ref<ActivityResponse[]>([]);
const activityCursor = ref<string | null>(null);
const activityHasMore = ref(false);
const activityLoadingMore = ref(false);

const page = computed(() => Math.floor(offset.value / LIMIT) + 1);
const isTrackActive = computed(() => activeType.value === "track");
const isActivityActive = computed(() => activeType.value === "activity");

const icon = "tag";

const entityPluralKeys: Record<string, string> = {
  artist: "browse.entities.artists",
  album: "browse.entities.albums",
  track: "browse.entities.tracks",
  playlist: "browse.entities.playlists",
  library: "browse.entities.libraries",
  activity: "browse.entities.activities",
};

const tabs = computed(() =>
  visibleTypes.value.map((type) => ({
    value: type,
    label: t(entityPluralKeys[type] ?? "browse.entities.item"),
  })),
);

const sortOptions = computed(() => [
  { value: "created_at", label: t("sort.fields.created_at") },
]);

function queryType(): string {
  const raw = route.query.type;
  const value = Array.isArray(raw) ? raw[0] : raw;
  return typeof value === "string" ? value : "";
}

async function updateQueryType(type: string) {
  const current = queryType();
  if (current === type) return;
  await router.replace({ query: { ...route.query, type } });
}

async function fetchActivityPage(append: boolean, cursor: string | null) {
  if (!props.activityLoader) return;

  const result = await props.activityLoader(props.name, {
    limit: LIMIT,
    cursor: cursor ?? undefined,
  });

  if (append) {
    activities.value = [...activities.value, ...result.activities];
  } else {
    activities.value = result.activities;
  }
  activityCursor.value = result.next_cursor ?? null;
  activityHasMore.value = !!result.next_cursor;
}

async function fetchItems() {
  error.value = null;

  try {
    if (activeType.value === "track") {
      const result = await listTracksWithMeta({
        limit: LIMIT,
        offset: offset.value,
        sort_by: sortBy.value,
        sort_dir: sortDir.value,
        include: "artist,album",
        ...(props.kind === "genre"
          ? { genre: props.name }
          : { tag: props.name }),
      });
      tracks.value = result.tracks;
      total.value = result.total;
      offset.value = result.offset;
      items.value = [];
    } else if (activeType.value === "activity" && props.activityLoader) {
      await fetchActivityPage(false, null);
    } else {
      const result = await props.loadItems(props.name, {
        limit: LIMIT,
        offset: offset.value,
        sort_by: sortBy.value,
        sort_dir: sortDir.value,
        type: activeType.value,
      });
      items.value = result.items;
      total.value = result.total;
      offset.value = result.offset;
      tracks.value = [];
    }
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  }
}

async function load() {
  if (loading.value || !activeType.value) return;

  loading.value = true;
  await fetchItems();
  loading.value = false;
}

async function loadVisibleTypes() {
  if (loading.value) return;

  loading.value = true;
  error.value = null;
  items.value = [];
  tracks.value = [];
  activities.value = [];
  total.value = 0;
  offset.value = 0;

  try {
    const counts = await Promise.all(
      props.availableTypes.map(async (type) => {
        if (type === "activity" && props.activityLoader) {
          const result = await props.activityLoader(props.name, { limit: 1 });
          return { type, total: result.activities.length };
        }
        const result = await props.loadItems(props.name, {
          limit: 1,
          offset: 0,
          sort_by: "created_at",
          sort_dir: "desc",
          type,
        });
        return { type, total: result.total };
      }),
    );

    visibleTypes.value = counts
      .filter(({ total }) => total > 0)
      .map(({ type }) => type);

    if (visibleTypes.value.length === 0) {
      loading.value = false;
      return;
    }

    const preferred = visibleTypes.value.find((type) => type === queryType());
    activeType.value = preferred ?? visibleTypes.value[0];
    sortBy.value = "created_at";
    sortDir.value = "desc";
    await fetchItems();
    await updateQueryType(activeType.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function onTabChange(type: string) {
  if (type === activeType.value) return;

  activeType.value = type;
  offset.value = 0;
  sortBy.value = "created_at";
  sortDir.value = "desc";
  items.value = [];
  tracks.value = [];
  activities.value = [];
  activityCursor.value = null;
  activityHasMore.value = false;
  await updateQueryType(type);
  void load();
}

async function loadMoreActivities() {
  if (
    activityLoadingMore.value ||
    !activityHasMore.value ||
    !activityCursor.value
  )
    return;

  activityLoadingMore.value = true;
  error.value = null;
  try {
    await fetchActivityPage(true, activityCursor.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
    activityHasMore.value = false;
  } finally {
    activityLoadingMore.value = false;
  }
}

function onPageChange(nextPage: number) {
  offset.value = (nextPage - 1) * LIMIT;
  void load();
}

function onSort(field: string, direction: "asc" | "desc") {
  sortBy.value = field;
  sortDir.value = direction;
  offset.value = 0;
  void load();
}

async function onDelete() {
  if (!authStore.isAdmin) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t(`${props.kind}s.deleteConfirm`, { name: props.name }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  deleting.value = true;
  try {
    await props.deleteItem(props.name);
    toast.push({
      type: "success",
      message: t(`${props.kind}s.deleteSuccess`),
    });
    await router.push(`/${props.kind}s`);
  } catch (err) {
    toast.push({
      type: "error",
      message: t(`${props.kind}s.deleteError`, {
        message: getApiErrorMessage(err),
      }),
    });
  } finally {
    deleting.value = false;
  }
}

function retry() {
  if (visibleTypes.value.length === 0) {
    void loadVisibleTypes();
  } else {
    void load();
  }
}

watch(
  () => route.query.type,
  (newType) => {
    const value = Array.isArray(newType) ? newType[0] : newType;
    const type = typeof value === "string" ? value : "";
    if (
      !type ||
      !visibleTypes.value.includes(type) ||
      activeType.value === type
    ) {
      return;
    }
    activeType.value = type;
    offset.value = 0;
    sortBy.value = "created_at";
    sortDir.value = "desc";
    items.value = [];
    tracks.value = [];
    activities.value = [];
    activityCursor.value = null;
    activityHasMore.value = false;
    void load();
  },
);

watch(
  () => props.name,
  () => {
    visibleTypes.value = [];
    activeType.value = "";
    sortBy.value = "created_at";
    sortDir.value = "desc";
    activities.value = [];
    activityCursor.value = null;
    activityHasMore.value = false;
    void loadVisibleTypes();
  },
);

onMounted(() => loadVisibleTypes());
</script>

<template>
  <div class="tag-detail-view">
    <div class="tag-detail-view__header">
      <div class="tag-detail-view__title-row">
        <AppPageTitle :icon="icon" class="tag-detail-view__title">
          {{ name }}
        </AppPageTitle>

        <AppButton
          v-if="authStore.isAdmin"
          variant="danger"
          size="sm"
          icon="trash"
          :loading="deleting"
          @click="onDelete"
        >
          {{ t("common.delete") }}
        </AppButton>
      </div>

      <div class="tag-detail-view__controls">
        <AppTabs
          :model-value="activeType"
          :tabs="tabs"
          @update:model-value="onTabChange"
        />

        <SortControl
          v-if="!isActivityActive"
          :model-value="sortBy"
          :direction="sortDir"
          :options="sortOptions"
          :show-field="false"
          @update:model-value="(field) => onSort(field, sortDir)"
          @update:direction="(dir) => onSort(sortBy, dir)"
        />
      </div>
    </div>

    <div
      v-if="
        loading &&
        !isTrackActive &&
        !isActivityActive &&
        items.length === 0 &&
        activities.length === 0
      "
      class="tag-detail-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="tag-detail-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="!isTrackActive && !isActivityActive && items.length === 0"
      class="tag-detail-view__empty"
      role="status"
    >
      {{ t(`${kind}s.emptyItems`) }}
    </div>

    <TrackList
      v-else-if="isTrackActive"
      :tracks="tracks"
      :loading="loading"
      :show-artwork="true"
      :deletable="authStore.isAdmin"
      :auto-scroll="true"
      @removed="load"
      @updated="load"
    />

    <template v-else-if="isActivityActive">
      <div
        v-if="!loading && activities.length === 0"
        class="tag-detail-view__empty"
        role="status"
      >
        {{ t("activities.empty") }}
      </div>

      <div v-else class="tag-detail-view__activities" role="list">
        <ActivityCard
          v-for="activity in activities"
          :key="activity.id"
          :activity="activity"
        />
      </div>

      <AppButton
        v-if="activityHasMore"
        variant="secondary"
        class="tag-detail-view__more"
        :loading="activityLoadingMore"
        @click="loadMoreActivities"
      >
        {{ t("activities.loadMore") }}
      </AppButton>
    </template>

    <template v-else>
      <div class="tag-detail-view__grid" role="list">
        <TaggedItemCard
          v-for="item in items"
          :id="item.id"
          :key="`${item.type}:${item.id}`"
          :type="item.type"
        />
      </div>
    </template>

    <AppPagination
      v-if="!isActivityActive && total > LIMIT"
      :page="page"
      :total="total"
      :per-page="LIMIT"
      @update:page="onPageChange"
    />
  </div>
</template>

<style scoped>
.tag-detail-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.tag-detail-view__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.tag-detail-view__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.tag-detail-view__title {
  margin: 0;
  font-size: 2rem;
  word-break: break-word;
}

.tag-detail-view__controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.tag-detail-view__skeleton {
  min-height: 16rem;
}

.tag-detail-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.tag-detail-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.tag-detail-view__activities {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.tag-detail-view__more {
  align-self: stretch;
}

.tag-detail-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}
</style>
