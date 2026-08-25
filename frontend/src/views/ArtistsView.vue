<script setup lang="ts">
import { onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listArtists, deleteArtist, type ArtistResponse } from "@/api/artists";
import ArtistCard from "@/components/library/ArtistCard.vue";
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
} = useEntityList<ArtistResponse>(listArtists);

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
      :recursive="true"
      :recursive-label="
        t('browse.delete.recursive', {
          contents: `${t('browse.entities.albums')} / ${t('browse.entities.tracks')}`,
        })
      "
    >
      <template #card="{ item, bulkMode }">
        <ArtistCard
          class="artists-view__card"
          :class="{ 'artists-view__card--selectable': bulkMode }"
          :artist="item"
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
