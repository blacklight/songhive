<script setup lang="ts">
import { onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listAlbums, type AlbumResponse } from "@/api/albums";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AlbumCard from "@/components/library/AlbumCard.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();
const { items, loading, error, query, hasMore, load, loadMore, search, retry } =
  useEntityList<AlbumResponse>(listAlbums);

onMounted(() => load());
</script>

<template>
  <div class="albums-view">
    <AppPageTitle class="albums-view__title" icon="compact-disc">
      {{ t("nav.albums") }}
    </AppPageTitle>

    <!--
      :debounce="0" avoids stacking with useEntityList's 300 ms debounce;
      the composable owns the real debounce.
    -->
    <SearchBar
      :model-value="query"
      :debounce="0"
      class="albums-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.albums'),
        })
      "
      @update:model-value="search"
    />

    <div
      v-if="loading && items.length === 0"
      class="albums-view__grid albums-view__grid--skeleton"
    >
      <SkeletonLoader v-for="i in 8" :key="i" variant="card" />
    </div>

    <div v-else-if="error" class="albums-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div v-else-if="items.length === 0" class="albums-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.albums") }) }}
    </div>

    <div v-else class="albums-view__grid">
      <AlbumCard v-for="album in items" :key="album.id" :album="album" />
    </div>

    <div class="albums-view__footer">
      <AppButton
        v-if="hasMore"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        icon="chevron-down"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
      <AppSpinner v-else-if="loading" />
    </div>
  </div>
</template>

<style scoped>
.albums-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.albums-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.albums-view__search {
  max-width: 32rem;
}

.albums-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}

.albums-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.albums-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.albums-view__footer {
  display: flex;
  justify-content: center;
}
</style>
