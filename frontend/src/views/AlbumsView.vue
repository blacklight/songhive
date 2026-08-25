<script setup lang="ts">
import { onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listAlbums, deleteAlbum, type AlbumResponse } from "@/api/albums";
import AlbumCard from "@/components/library/AlbumCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";

const { t } = useI18n();
const {
  items,
  loading,
  error,
  query,
  hasMore,
  load,
  loadMore,
  search,
  retry,
  refresh,
} = useEntityList<AlbumResponse>(listAlbums);

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
      :recursive="true"
      :recursive-label="
        t('browse.delete.recursive', { contents: t('browse.entities.tracks') })
      "
    >
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

.albums-view__card {
  display: block;
}

.albums-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
}
</style>
