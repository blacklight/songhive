<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listFavorites,
  removeFavorite,
  type FavoriteResponse,
} from "@/api/favorites";
import { getTrack, type TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { useShareDialog } from "@/composables/useShareDialog";
import { useTrackEnrichment } from "@/composables/useTrackEnrichment";
import type { QueueTrack } from "@/player/types";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";

const { t } = useI18n();
const toast = useToastStore();
const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

const favorites = ref<FavoriteResponse[]>([]);
const tracks = ref<TrackResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const limit = 20;
const hasMore = ref(false);
const activeTab = ref<"tracks" | "albums" | "artists" | "playlists">("tracks");

const { enrich: trackEnrich } = useTrackEnrichment(tracks);

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function load(initial = false) {
  const offset = initial ? 0 : favorites.value.length;

  loading.value = true;
  if (initial) {
    error.value = null;
  }

  try {
    const batch = await listFavorites({ limit, offset });
    if (initial) {
      favorites.value = [];
      tracks.value = [];
    }

    const resolved = await Promise.all(
      batch.map((favorite) => getTrack(favorite.track_id).catch(() => null)),
    );
    const newTracks = resolved.filter(
      (track): track is TrackResponse => track !== null,
    );

    // Reassign (rather than push) so the useTrackEnrichment watcher, which
    // is shallow by default, fires for both the initial load and loadMore.
    favorites.value = [...favorites.value, ...batch];
    tracks.value = [...tracks.value, ...newTracks];

    // hasMore is derived from the raw favorites count, before any tracks fail
    // to resolve, so pagination does not stop early on missing tracks.
    hasMore.value = batch.length === limit;
  } catch (err) {
    if (initial) {
      error.value = getErrorMessage(err);
    } else {
      // A loadMore failure should not hide the already-loaded list or reset
      // pagination. Surface the error as a toast and leave the page as-is.
      toast.push({ type: "error", message: getErrorMessage(err) });
    }
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  await load();
}

async function retry() {
  await load(true);
}

async function onToggleFavorite(track: QueueTrack) {
  try {
    await removeFavorite(track.id);
    tracks.value = tracks.value.filter((t) => t.id !== track.id);
    favorites.value = favorites.value.filter(
      (favorite) => favorite.track_id !== track.id,
    );
    toast.push({
      type: "success",
      message: t("pages.favorites.removeSuccess"),
    });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.favorites.removeError", {
        message: getErrorMessage(err),
      }),
    });
  }
}

function onTrackShare(track: QueueTrack) {
  openShare("track", track.id, track.title, track.owner_id ?? null);
}

async function onTracksRemoved(trackIds: string[]) {
  const removed = new Set(trackIds);
  tracks.value = tracks.value.filter((track) => !removed.has(track.id));
  favorites.value = favorites.value.filter(
    (favorite) => !removed.has(favorite.track_id),
  );
}

const tabs = [
  { key: "tracks" as const, label: t("pages.favorites.tracks") },
  { key: "albums" as const, label: t("pages.favorites.albums") },
  { key: "artists" as const, label: t("pages.favorites.artists") },
  { key: "playlists" as const, label: t("pages.favorites.playlists") },
];

onMounted(() => load(true));
</script>

<template>
  <div class="favorites-view">
    <AppPageTitle class="favorites-view__title" icon="heart">{{
      t("pages.favorites.title")
    }}</AppPageTitle>

    <div class="favorites-view__tabs">
      <AppButton
        v-for="tab in tabs"
        :key="tab.key"
        size="sm"
        :variant="activeTab === tab.key ? 'primary' : 'ghost'"
        :disabled="tab.key !== 'tracks'"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </AppButton>
    </div>

    <p class="favorites-view__hint">
      {{ t("pages.favorites.entityTypesGated") }}
    </p>

    <template v-if="activeTab === 'tracks'">
      <div v-if="error" class="favorites-view__error" role="alert">
        <span>{{ error }}</span>
        <AppButton size="sm" icon="rotate-right" @click="retry">{{
          t("common.retry")
        }}</AppButton>
      </div>

      <div
        v-else-if="loading && tracks.length === 0"
        class="favorites-view__skeleton"
      >
        <SkeletonLoader variant="page" />
      </div>

      <div v-else-if="tracks.length === 0" class="favorites-view__empty">
        {{
          favorites.length === 0
            ? t("pages.favorites.empty")
            : t("pages.favorites.emptyWithFavorites")
        }}
      </div>

      <TrackList
        v-else
        :tracks="tracks"
        :loading="loading"
        :enrich="trackEnrich"
        :favorite-label="t('common.unfavorite')"
        :deletable="true"
        @toggle-favorite="onToggleFavorite"
        @share="onTrackShare"
        @removed="onTracksRemoved"
      />

      <div v-if="!error && hasMore" class="favorites-view__footer">
        <AppButton
          icon="chevron-down"
          variant="secondary"
          :loading="loading"
          :disabled="loading"
          @click="loadMore"
        >
          {{ t("browse.list.loadMore") }}
        </AppButton>
      </div>
    </template>

    <ShareDialog
      v-if="shareTarget"
      :open="shareOpen"
      :item-type="shareTarget.itemType"
      :item-id="shareTarget.itemId"
      :title="shareTarget.title"
      :owner-id="shareTarget.ownerId"
      @close="closeShare"
    />
  </div>
</template>

<style scoped>
.favorites-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.favorites-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.favorites-view__tabs {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.favorites-view__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
}

.favorites-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.favorites-view__skeleton {
  min-height: 16rem;
}

.favorites-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.favorites-view__footer {
  display: flex;
  justify-content: center;
}
</style>
