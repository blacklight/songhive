<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listAlbums, deleteAlbum, type AlbumResponse } from "@/api/albums";
import { useAuthStore } from "@/stores/auth";
import AlbumCard from "@/components/library/AlbumCard.vue";
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
      :items="items"
      :loading="loading"
      :error="error"
      :has-more="hasMore"
      :query="query"
      :entity-singular="t('browse.entities.album')"
      :entity-plural="t('browse.entities.albums')"
      :delete-one="deleteAlbum"
      :refresh="refresh"
      :get-name="(album) => album.title"
      :search="search"
      :load-more="loadMore"
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
        <AlbumCard
          class="albums-view__card"
          :class="{ 'albums-view__card--selectable': bulkMode }"
          :album="item"
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
