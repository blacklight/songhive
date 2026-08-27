<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useChunkList } from "@/composables/useChunkList";
import { useShareDialog } from "@/composables/useShareDialog";
import { removeFavorite } from "@/api/favorites";
import { listTracksWithMeta, type TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import type { QueueTrack } from "@/player/types";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";
import SortControl from "@/components/ui/SortControl.vue";

const { t } = useI18n();
const toast = useToastStore();
const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

const {
  items,
  loading,
  error,
  query,
  hasMore,
  total,
  sortBy,
  sortDir,
  load,
  loadMore,
  search,
  setSort,
  retry,
} = useChunkList<TrackResponse>(
  async (params) => {
    const result = await listTracksWithMeta({
      ...params,
      favorited: true,
      include: "artist,album",
    });
    return { items: result.tracks, offset: result.offset, total: result.total };
  },
  {
    defaultSortBy: "created_at",
    defaultSortDir: "desc",
    syncQuery: true,
  },
);

const activeTab = ref<"tracks" | "albums" | "artists" | "playlists">("tracks");

const sortOptions = computed(() => [
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "title", label: t("sort.fields.title") },
  { value: "artist_name", label: t("sort.fields.artist_name") },
  { value: "album_title", label: t("sort.fields.album_title") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
  { value: "release_year", label: t("sort.fields.release_year") },
]);

const tabs = [
  { key: "tracks" as const, label: t("pages.favorites.tracks") },
  { key: "albums" as const, label: t("pages.favorites.albums") },
  { key: "artists" as const, label: t("pages.favorites.artists") },
  { key: "playlists" as const, label: t("pages.favorites.playlists") },
];

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function onToggleFavorite(track: QueueTrack) {
  try {
    await removeFavorite(track.id);
    items.value = items.value.filter((t) => t.id !== track.id);
    total.value = Math.max(0, total.value - 1);
    toast.push({
      type: "success",
      message: t("pages.favorites.removeSuccess"),
    });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.favorites.removeError", {
        message: getErrorMessage(err),
      }),
    });
  }
}

function onTrackShare(track: QueueTrack) {
  openShare(
    "track",
    track.id,
    track.title,
    track.owner_id ?? null,
    track.visibility,
  );
}

async function onTracksRemoved(trackIds: string[]) {
  const removed = new Set(trackIds);
  items.value = items.value.filter((track) => !removed.has(track.id));
  total.value = Math.max(0, total.value - trackIds.length);
}

onMounted(() => load());
</script>

<template>
  <div class="favorites-view">
    <AppPageTitle class="favorites-view__title" icon="heart">{{
      t("pages.favorites.title")
    }}</AppPageTitle>

    <div class="favorites-view__tabs">
      <AppButton
        v-for="tab in tabs"
        :key="tab.key"
        size="sm"
        :variant="activeTab === tab.key ? 'primary' : 'ghost'"
        :disabled="tab.key !== 'tracks'"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </AppButton>
    </div>

    <p class="favorites-view__hint">
      {{ t("pages.favorites.entityTypesGated") }}
    </p>

    <template v-if="activeTab === 'tracks'">
      <div class="favorites-view__controls">
        <SortControl
          :model-value="sortBy"
          :direction="sortDir"
          :options="sortOptions"
          @update:model-value="(field) => setSort(field, sortDir)"
          @update:direction="(dir) => setSort(sortBy, dir)"
        />
        <SearchBar
          :model-value="query"
          :debounce="0"
          class="favorites-view__search"
          :placeholder="
            t('browse.list.searchPlaceholder', {
              entity: t('browse.entities.tracks'),
            })
          "
          @update:model-value="search"
        />
      </div>

      <div v-if="error" class="favorites-view__error" role="alert">
        <span>{{ error }}</span>
        <AppButton size="sm" icon="rotate-right" @click="retry">{{
          t("common.retry")
        }}</AppButton>
      </div>

      <div
        v-else-if="loading && items.length === 0"
        class="favorites-view__skeleton"
      >
        <SkeletonLoader variant="page" />
      </div>

      <div v-else-if="items.length === 0" class="favorites-view__empty">
        {{ t("pages.favorites.empty") }}
      </div>

      <TrackList
        v-else
        :tracks="items"
        :loading="loading"
        :favorite-label="t('common.unfavorite')"
        :deletable="true"
        @toggle-favorite="onToggleFavorite"
        @share="onTrackShare"
        @removed="onTracksRemoved"
      />

      <div v-if="!error && hasMore" class="favorites-view__footer">
        <AppButton
          icon="chevron-down"
          variant="secondary"
          :loading="loading"
          :disabled="loading"
          @click="loadMore"
        >
          {{ t("browse.list.loadMore") }}
        </AppButton>
      </div>
    </template>

    <ShareDialog
      v-if="shareTarget"
      :open="shareOpen"
      :item-type="shareTarget.itemType"
      :item-id="shareTarget.itemId"
      :title="shareTarget.title"
      :owner-id="shareTarget.ownerId"
      :visibility="shareTarget.visibility"
      @close="closeShare"
    />
  </div>
</template>

<style scoped>
.favorites-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.favorites-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.favorites-view__tabs {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.favorites-view__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
}

.favorites-view__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--space-3);
}

.favorites-view__search {
  max-width: 32rem;
  flex: 1;
}

.favorites-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.favorites-view__skeleton {
  min-height: 16rem;
}

.favorites-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.favorites-view__footer {
  display: flex;
  justify-content: center;
}
</style>
