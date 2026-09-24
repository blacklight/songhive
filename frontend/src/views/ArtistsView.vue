<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listArtists, deleteArtist, type ArtistResponse } from "@/api/artists";
import { useAuthStore } from "@/stores/auth";
import ArtistCard from "@/components/library/ArtistCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";
import RemoteCollectionSection from "@/components/library/RemoteCollectionSection.vue";

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
      :items="items"
      :loading="loading"
      :error="error"
      :has-more="hasMore"
      :query="query"
      :entity-singular="t('browse.entities.artist')"
      :entity-plural="t('browse.entities.artists')"
      :delete-one="deleteArtist"
      :refresh="refresh"
      :get-name="(artist) => artist.name"
      :search="search"
      :load-more="loadMore"
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
        <ArtistCard
          class="artists-view__card"
          :class="{ 'artists-view__card--selectable': bulkMode }"
          :artist="item"
        />
      </template>
    </BulkEditableGrid>
    <RemoteCollectionSection kind="artist" :active="myCollection" />
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
