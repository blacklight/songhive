<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { useRemoteEntities } from "@/composables/useRemoteEntities";
import { listArtists, deleteArtist, type ArtistResponse } from "@/api/artists";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
  type EntityListItem,
} from "@/utils/remoteEntities";
import { useAuthStore } from "@/stores/auth";
import ArtistCard from "@/components/library/ArtistCard.vue";
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
} = useEntityList<ArtistResponse>(
  (params) =>
    listArtists({
      ...params,
      collection: myCollection.value || undefined,
    }),
  {
    defaultSortBy: "name",
    syncQuery: true,
  },
);

// Federated artists render in the same grid, marked by RemoteEntityCard's
// globe badge and domain line. The remote fetch follows the same
// search/collection/sort state as the local list and pages in lockstep —
// Load More pulls the next page of each side.
const {
  items: remoteItems,
  hasMore: remoteHasMore,
  loadMore: loadMoreRemote,
} = useRemoteEntities("artist", {
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

const displayItems = computed<EntityListItem<ArtistResponse>[]>(() =>
  mergeEntityItems(items.value, remoteItems.value, {
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
    keyOf: (item, field) => {
      if (item.remote) return remoteEntitySortKey(item.entity, field);
      const artist = item.entity;
      if (field === "created_at") return artist.created_at ?? null;
      if (field === "updated_at") return artist.updated_at ?? null;
      return artist.name;
    },
    // A remote artist sharing a name with a local one clusters right
    // under it rather than scattering across the grid.
    groupKeyOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
  }),
);

const sortOptions = computed(() => [
  { value: "name", label: t("sort.fields.name") },
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
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
  <div class="artists-view">
    <BulkEditableGrid
      :title="t('nav.artists')"
      icon="users"
      :items="displayItems"
      :loading="loading"
      :error="error"
      :has-more="mergedHasMore"
      :query="query"
      :entity-singular="t('browse.entities.artist')"
      :entity-plural="t('browse.entities.artists')"
      :delete-one="deleteArtist"
      :refresh="refresh"
      :get-name="
        (item) =>
          item.remote ? remoteEntityName(item.entity) : item.entity.name
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
        t('browse.delete.recursive', {
          contents: `${t('browse.entities.albums')} / ${t('browse.entities.tracks')}`,
        })
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
          class="artists-view__card"
          :object="item.entity"
          variant="avatar"
        />
        <ArtistCard
          v-else
          class="artists-view__card"
          :class="{ 'artists-view__card--selectable': bulkMode }"
          :artist="item.entity"
        />
      </template>
    </BulkEditableGrid>
  </div>
</template>

<style scoped>
.artists-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artists-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
}
</style>
