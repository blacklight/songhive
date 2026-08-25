<script setup lang="ts">
import { onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listArtists, type ArtistResponse } from "@/api/artists";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ArtistCard from "@/components/library/ArtistCard.vue";

const { t } = useI18n();
const { items, loading, error, query, hasMore, load, loadMore, search, retry } =
  useEntityList<ArtistResponse>(listArtists);

onMounted(() => load());
</script>

<template>
  <div class="artists-view">
    <AppPageTitle class="artists-view__title" icon="users">{{
      t("nav.artists")
    }}</AppPageTitle>

    <!--
      :debounce="0" avoids stacking with useEntityList's 300 ms debounce;
      the composable owns the real debounce.
    -->
    <SearchBar
      :model-value="query"
      :debounce="0"
      class="artists-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.artists'),
        })
      "
      @update:model-value="search"
    />

    <div
      v-if="loading && items.length === 0"
      class="artists-view__grid artists-view__grid--skeleton"
    >
      <SkeletonLoader v-for="i in 8" :key="i" variant="card" />
    </div>

    <div v-else-if="error" class="artists-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <div v-else-if="items.length === 0" class="artists-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.artists") }) }}
    </div>

    <div v-else class="artists-view__grid">
      <ArtistCard v-for="artist in items" :key="artist.id" :artist="artist" />
    </div>

    <div class="artists-view__footer">
      <AppButton
        v-if="hasMore"
        icon="chevron-down"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
      <AppSpinner v-else-if="loading" />
    </div>
  </div>
</template>

<style scoped>
.artists-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artists-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.artists-view__search {
  max-width: 32rem;
}

.artists-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}

.artists-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.artists-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.artists-view__footer {
  display: flex;
  justify-content: center;
}
</style>
