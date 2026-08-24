<script setup lang="ts">
import { onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listTracks, type TrackResponse } from "@/api/tracks";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import TrackList from "@/components/library/TrackList.vue";

const { t } = useI18n();
const { items, loading, error, query, hasMore, load, loadMore, search, retry } =
  useEntityList<TrackResponse>(listTracks);

onMounted(() => load());
</script>

<template>
  <div class="tracks-view">
    <h1 class="tracks-view__title">{{ t("nav.tracks") }}</h1>

    <!--
      :debounce="0" avoids stacking with useEntityList's 300 ms debounce;
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

    <div v-if="error" class="tracks-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="retry">{{ t("common.retry") }}</AppButton>
    </div>

    <TrackList v-else :tracks="items" :loading="loading" />

    <div class="tracks-view__footer">
      <AppButton
        v-if="hasMore"
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
.tracks-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.tracks-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.tracks-view__search {
  max-width: 32rem;
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

.tracks-view__footer {
  display: flex;
  justify-content: center;
}
</style>
