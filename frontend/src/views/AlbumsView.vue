<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { useRemoteEntities } from "@/composables/useRemoteEntities";
import { listAlbums, deleteAlbum, type AlbumResponse } from "@/api/albums";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
  type EntityListItem,
} from "@/utils/remoteEntities";
import { useAuthStore } from "@/stores/auth";
import AlbumCard from "@/components/library/AlbumCard.vue";
import RemoteEntityCard from "@/components/library/RemoteEntityCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const myCollection = ref(authStore.isAuthenticated);
const {
  items,
  loading,
  error,
  query,
  hasMore,
  sortBy,
  sortDir,
  load,
  loadMore,
  search,
  setSort,
  retry,
  refresh,
} = useEntityList<AlbumResponse>(
  (params) =>
    listAlbums({
      ...params,
      include: "artist",
      collection: myCollection.value || undefined,
    }),
  {
    defaultSortBy: "title",
    syncQuery: true,
  },
);

// Federated albums render in the same grid, marked by RemoteEntityCard's
// globe badge and domain line. The remote fetch follows the same
// search/collection/sort state as the local list and pages in lockstep —
// Load More pulls the next page of each side.
const {
  items: remoteItems,
  hasMore: remoteHasMore,
  loadMore: loadMoreRemote,
} = useRemoteEntities("album", {
  collection: myCollection,
  query,
  sortBy,
  sortDir,
});

const mergedHasMore = computed(() => hasMore.value || remoteHasMore.value);

function loadMoreEntities() {
  void loadMore();
  void loadMoreRemote();
}

const displayItems = computed<EntityListItem<AlbumResponse>[]>(() =>
  mergeEntityItems(items.value, remoteItems.value, {
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.title,
    keyOf: (item, field) => {
      if (item.remote) return remoteEntitySortKey(item.entity, field);
      const album = item.entity;
      switch (field) {
        case "artist_name":
          return album.artist?.name ?? null;
        case "release_year":
          return album.release_year ?? null;
        case "created_at":
          return album.created_at ?? null;
        case "updated_at":
          return album.updated_at ?? null;
        default:
          return album.title;
      }
    },
    // A remote album matching a local one's title and artist clusters
    // right under it rather than scattering across the grid.
    groupKeyOf: (item) =>
      item.remote
        ? `${remoteEntityName(item.entity)}|${item.entity.artist_name ?? ""}`
        : `${item.entity.title}|${item.entity.artist?.name ?? ""}`,
  }),
);

const sortOptions = computed(() => [
  { value: "title", label: t("sort.fields.title") },
  { value: "artist_name", label: t("sort.fields.artist_name") },
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
  { value: "release_year", label: t("sort.fields.release_year") },
]);

function onSort(field: string, direction: "asc" | "desc") {
  void setSort(field, direction);
}

function onMyCollectionChange(value: boolean) {
  myCollection.value = value;
  void refresh();
}

onMounted(() => load());
</script>

<template>
  <div class="albums-view">
    <BulkEditableGrid
      :title="t('nav.albums')"
      icon="compact-disc"
      :items="displayItems"
      :loading="loading"
      :error="error"
      :has-more="mergedHasMore"
      :query="query"
      :entity-singular="t('browse.entities.album')"
      :entity-plural="t('browse.entities.albums')"
      :delete-one="deleteAlbum"
      :refresh="refresh"
      :get-name="
        (item) =>
          item.remote ? remoteEntityName(item.entity) : item.entity.title
      "
      :can-manage="(item) => !item.remote"
      :search="search"
      :load-more="loadMoreEntities"
      :retry="retry"
      :sort-by="sortBy"
      :sort-dir="sortDir"
      :sort-options="sortOptions"
      :recursive="true"
      :recursive-label="
        t('browse.delete.recursive', { contents: t('browse.entities.tracks') })
      "
      @sort="onSort"
    >
      <template #filters>
        <CollectionToggle
          :model-value="myCollection"
          @update:model-value="onMyCollectionChange"
        />
      </template>

      <template #card="{ item, bulkMode }">
        <RemoteEntityCard
          v-if="item.remote"
          class="albums-view__card"
          :object="item.entity"
        />
        <AlbumCard
          v-else
          class="albums-view__card"
          :class="{ 'albums-view__card--selectable': bulkMode }"
          :album="item.entity"
        />
      </template>
    </BulkEditableGrid>
  </div>
</template>

<style scoped>
.albums-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.albums-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
}
</style>
