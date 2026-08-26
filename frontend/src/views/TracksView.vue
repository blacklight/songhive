<script setup lang="ts">
import { onMounted, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useChunkList } from "@/composables/useChunkList";
import { useShareDialog } from "@/composables/useShareDialog";
import { listTracksWithMeta, type TrackResponse } from "@/api/tracks";
import type { QueueTrack } from "@/player/types";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const player = usePlayerStore();
const {
  items,
  loading,
  error,
  query,
  hasMore,
  hasPrevious,
  load,
  loadMore,
  loadPrevious,
  loadAround,
  search,
  retry,
  refresh,
} = useChunkList<TrackResponse>(async (params) => {
  const result = await listTracksWithMeta({
    ...params,
    include: "artist,album",
  });
  return { items: result.tracks, offset: result.offset, total: result.total };
});
const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

function onTrackShare(track: QueueTrack) {
  openShare(
    "track",
    track.id,
    track.title,
    track.owner_id ?? null,
    track.visibility,
  );
}

async function onRemoved() {
  await refresh();
}

onMounted(() => {
  if (player.currentTrack?.id) {
    void loadAround(player.currentTrack.id);
  } else {
    void load();
  }
});

watch(
  () => player.currentTrack?.id,
  (currentTrackId, previousTrackId) => {
    if (!currentTrackId || currentTrackId === previousTrackId) return;
    const current = player.currentTrack;
    if (current && !items.value.some((t) => t.id === current.id)) {
      void loadAround(current.id);
    }
  },
);
</script>

<template>
  <div class="tracks-view">
    <AppPageTitle class="tracks-view__title" icon="music">{{
      t("nav.tracks")
    }}</AppPageTitle>

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
      <AppButton size="sm" icon="rotate-right" @click="retry">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else>
      <div v-if="hasPrevious" class="tracks-view__load-previous">
        <AppButton
          icon="chevron-up"
          variant="secondary"
          :loading="loading"
          :disabled="loading"
          @click="loadPrevious"
        >
          {{ t("browse.list.loadPrevious") }}
        </AppButton>
      </div>

      <TrackList
        :tracks="items"
        :loading="loading"
        :deletable="authStore.isAuthenticated"
        @share="onTrackShare"
        @removed="onRemoved"
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
      @close="closeShare"
    />

    <div class="tracks-view__footer">
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

.tracks-view__load-previous {
  display: flex;
  justify-content: center;
}

.tracks-view__footer {
  display: flex;
  justify-content: center;
}
</style>
