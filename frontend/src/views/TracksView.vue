<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useChunkList } from "@/composables/useChunkList";
import { useRemoteEntities } from "@/composables/useRemoteEntities";
import { useShareDialog } from "@/composables/useShareDialog";
import { listTracksWithMeta, type TrackResponse } from "@/api/tracks";
import type { QueueTrack } from "@/player/types";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
} from "@/utils/remoteEntities";
import { remoteObjectToQueueTrack } from "@/utils/remoteObject";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";
import SortControl from "@/components/ui/SortControl.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const player = usePlayerStore();
const myCollection = ref(authStore.isAuthenticated);
const {
  items,
  loading,
  loadingMore,
  loadingPrevious,
  error,
  query,
  hasMore,
  hasPrevious,
  total,
  sortBy,
  sortDir,
  load,
  loadMore,
  loadPrevious,
  loadAround,
  search,
  setSort,
  retry,
  refresh,
} = useChunkList<TrackResponse>(
  async (params) => {
    const result = await listTracksWithMeta({
      ...params,
      include: "artist,album",
      collection: myCollection.value || undefined,
    });
    return { items: result.tracks, offset: result.offset, total: result.total };
  },
  {
    defaultSortBy: "created_at",
    defaultSortDir: "desc",
    syncQuery: true,
  },
);
const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

// Federated tracks render in the same list, marked by TrackList's globe +
// domain badge. The remote fetch follows the same search/collection/sort
// state as the local list and pages in lockstep — Load More pulls the
// next page of each side so remote tracks keep arriving instead of
// sitting as a fixed first-page set.
const {
  items: remoteItems,
  hasMore: remoteHasMore,
  loadingMore: remoteLoadingMore,
  loadMore: loadMoreRemote,
} = useRemoteEntities("track", {
  collection: myCollection,
  query,
  sortBy,
  sortDir,
});

const mergedHasMore = computed(() => hasMore.value || remoteHasMore.value);
const mergedLoadingMore = computed(
  () => loadingMore.value || remoteLoadingMore.value,
);

function loadMoreEntities() {
  void loadMore();
  void loadMoreRemote();
}

const displayTracks = computed<TrackResponse[]>(() =>
  mergeEntityItems(items.value, remoteItems.value, {
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.title,
    keyOf: (item, field) => {
      if (item.remote) return remoteEntitySortKey(item.entity, field);
      const track = item.entity;
      switch (field) {
        case "artist_name":
          return track.artist?.name ?? null;
        case "album_title":
          return track.album?.title ?? null;
        case "release_year":
          return track.release_year ?? null;
        case "created_at":
          return track.created_at ?? null;
        case "updated_at":
          return track.updated_at ?? null;
        default:
          return track.title;
      }
    },
    // A remote track matching a local one's title and artist clusters
    // right under it rather than scattering across the list.
    groupKeyOf: (item) =>
      item.remote
        ? `${remoteEntityName(item.entity)}|${item.entity.artist_name ?? ""}`
        : `${item.entity.title}|${item.entity.artist?.name ?? ""}`,
  }).flatMap((item) => {
    if (!item.remote) return [item.entity];
    // QueueTrack extends TrackResponse, so the row carries its remote
    // flags (domain badge, remote page link) straight into TrackList.
    const track = remoteObjectToQueueTrack(item.entity, {
      requirePlayable: false,
    });
    return track ? [track] : [];
  }),
);

const sortOptions = computed(() => [
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "title", label: t("sort.fields.title") },
  { value: "artist_name", label: t("sort.fields.artist_name") },
  { value: "album_title", label: t("sort.fields.album_title") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
  { value: "release_year", label: t("sort.fields.release_year") },
]);

function onTrackShare(track: QueueTrack) {
  openShare(
    "track",
    track.id,
    track.title,
    track.owner_id ?? null,
    track.visibility,
    track.audio_url,
  );
}

async function onRemoved() {
  await refresh();
}

function onMyCollectionChange(value: boolean) {
  myCollection.value = value;
  void refresh();
}

onMounted(() => {
  const current = player.currentTrack;
  if (current?.id && !current.remote) {
    void loadAround(current.id);
  } else {
    void load();
  }
});

watch(
  () => player.currentTrack?.id,
  (currentTrackId, previousTrackId) => {
    if (!currentTrackId || currentTrackId === previousTrackId) return;
    const current = player.currentTrack;
    // Remote queue tracks carry remote_object ids — there is no local page
    // to center on, so skip the around-load for them.
    if (current?.remote) return;
    if (current && !items.value.some((t) => t.id === current.id)) {
      void loadAround(current.id);
    }
  },
);
</script>

<template>
  <div class="tracks-view">
    <AppPageTitle class="tracks-view__title" icon="music">
      {{ t("nav.tracks") }}
      <span v-if="total > 0 || !loading" class="tracks-view__count"
        >({{ total }})</span
      >
    </AppPageTitle>

    <div class="tracks-view__controls">
      <SortControl
        :model-value="sortBy"
        :direction="sortDir"
        :options="sortOptions"
        @update:model-value="(field) => setSort(field, sortDir)"
        @update:direction="(dir) => setSort(sortBy, dir)"
      />

      <CollectionToggle
        :model-value="myCollection"
        @update:model-value="onMyCollectionChange"
      />

      <!--
        :debounce="0" avoids stacking with useChunkList's 300 ms debounce;
        the composable owns the real debounce.
      -->
      <SearchBar
        :model-value="query"
        :debounce="0"
        class="tracks-view__search"
        :placeholder="
          t('browse.list.searchPlaceholder', {
            entity: t('browse.entities.tracks'),
          })
        "
        @update:model-value="search"
      />
    </div>

    <div v-if="error" class="tracks-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else>
      <div v-if="hasPrevious" class="tracks-view__load-previous">
        <AppButton
          icon="chevron-up"
          variant="secondary"
          :loading="loadingPrevious"
          :disabled="loading"
          @click="loadPrevious"
        >
          {{ t("browse.list.loadPrevious") }}
        </AppButton>
      </div>

      <TrackList
        :tracks="displayTracks"
        :loading="loading"
        :loading-more="loadingMore"
        :loading-previous="loadingPrevious"
        :deletable="authStore.isAuthenticated"
        @share="onTrackShare"
        @removed="onRemoved"
        @updated="onRemoved"
      />
    </template>

    <ShareDialog
      v-if="shareTarget"
      :open="shareOpen"
      :item-type="shareTarget.itemType"
      :item-id="shareTarget.itemId"
      :title="shareTarget.title"
      :owner-id="shareTarget.ownerId"
      :visibility="shareTarget.visibility"
      :download-url="shareTarget.downloadUrl"
      @close="closeShare"
    />

    <div class="tracks-view__footer">
      <AppButton
        v-if="mergedHasMore"
        icon="chevron-down"
        variant="secondary"
        :loading="mergedLoadingMore"
        :disabled="loading"
        @click="loadMoreEntities"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
      <AppSpinner v-else-if="loading" />
    </div>
  </div>
</template>

<style scoped>
.tracks-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.tracks-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.tracks-view__count {
  font-weight: normal;
  color: var(--color-text-muted);
}

.tracks-view__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: last baseline;
  gap: var(--space-3);
}

.tracks-view__search {
  max-width: 32rem;
  flex: 1;
}

.tracks-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.tracks-view__load-previous {
  display: flex;
  justify-content: center;
}

.tracks-view__footer {
  display: flex;
  justify-content: center;
}
</style>
