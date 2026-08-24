<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterLink } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import { getAlbum, type AlbumResponse } from "@/api/albums";
import { getArtist, type ArtistResponse } from "@/api/artists";
import { listTracks, type TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import type { TrackEnrich } from "@/player/enrich";
import { useEntityMeta } from "@/composables/useEntityMeta";
import AppButton from "@/components/ui/AppButton.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";

const { t } = useI18n();
const route = useRoute();
const albumId = computed(() => String(route.params.id));

const album = ref<AlbumResponse | null>(null);
const artist = ref<ArtistResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const {
  items: tracks,
  loading: tracksLoading,
  error: tracksError,
  hasMore: tracksHasMore,
  load: loadTracks,
  loadMore: loadMoreTracks,
  retry: retryTracks,
} = useEntityList<TrackResponse>((params: EntityListParams) =>
  listTracks({
    q: params.q,
    album_id: albumId.value,
    limit: params.limit,
    offset: params.offset,
  }),
);

const artistName = computed(() => artist.value?.name ?? "");

const trackEnrich = computed<Map<string, TrackEnrich>>(() => {
  const map = new Map<string, TrackEnrich>();
  for (const track of tracks.value) {
    map.set(track.id, {
      artist_name: artistName.value,
      album_title: album.value?.title,
      artwork_url: album.value?.cover_url ?? undefined,
    });
  }
  return map;
});

const { ownerName, visibilityText } = useEntityMeta(album);

async function loadAlbum() {
  loading.value = true;
  error.value = null;
  try {
    album.value = await getAlbum(albumId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function loadArtist() {
  if (!album.value?.artist_id) return;
  try {
    artist.value = await getArtist(album.value.artist_id);
  } catch {
    artist.value = null;
  }
}

async function load() {
  album.value = null;
  artist.value = null;
  error.value = null;
  await loadAlbum();
  if (!album.value) return;
  await Promise.all([loadArtist(), loadTracks(true)]);
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="album-view">
    <div v-if="loading && !album" class="album-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="album-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="load">{{ t("common.retry") }}</AppButton>
    </div>

    <template v-else-if="album">
      <div class="album-view__header">
        <img
          v-if="album.cover_url"
          :src="album.cover_url"
          :alt="album.title"
          class="album-view__cover"
        />
        <AppAvatar
          v-else
          :name="album.title"
          size="lg"
          class="album-view__cover"
        />

        <div class="album-view__info">
          <h1 class="album-view__title">{{ album.title }}</h1>

          <RouterLink
            v-if="artist && album.artist_id"
            :to="`/artists/${album.artist_id}`"
            class="album-view__artist"
          >
            {{ artist.name }}
          </RouterLink>
          <span v-else-if="album.artist_id" class="album-view__artist">
            {{ t("browse.entities.artist") }}
          </span>

          <div class="album-view__meta">
            <span v-if="album.release_year" class="album-view__meta-item">
              {{ t("browse.detail.year") }} {{ album.release_year }}
            </span>
            <span class="album-view__meta-item">
              {{ t("browse.detail.visibility") }} {{ visibilityText }}
            </span>
            <span v-if="ownerName" class="album-view__meta-item">
              {{ t("browse.detail.owner") }} {{ ownerName }}
            </span>
          </div>

          <p v-if="album.description" class="album-view__description">
            {{ album.description }}
          </p>
        </div>
      </div>

      <section
        class="album-view__section"
        aria-labelledby="album-tracks-heading"
      >
        <h2 id="album-tracks-heading" class="album-view__section-title">
          {{ t("browse.detail.tracks") }}
        </h2>

        <div v-if="tracksError" class="album-view__section-error" role="alert">
          <span>{{ tracksError }}</span>
          <AppButton size="sm" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :context="artistName"
          :enrich="trackEnrich"
          :show-artwork="true"
        />

        <div class="album-view__footer">
          <AppButton
            v-if="tracksHasMore"
            variant="secondary"
            :loading="tracksLoading"
            :disabled="tracksLoading"
            @click="loadMoreTracks"
          >
            {{ t("browse.list.loadMore") }}
          </AppButton>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.album-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.album-view__skeleton {
  min-height: 16rem;
}

.album-view__error,
.album-view__section-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.album-view__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-5);
  flex-wrap: wrap;
}

.album-view__cover {
  width: 12rem;
  height: 12rem;
  border-radius: var(--radius-lg);
  object-fit: cover;
}

.album-view__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  flex: 1;
  min-width: 16rem;
}

.album-view__title {
  margin: 0;
  font-size: 2rem;
}

.album-view__artist {
  color: var(--color-accent-contrast);
  text-decoration: none;
  font-size: 1.125rem;
}

.album-view__artist:hover {
  text-decoration: underline;
}

.album-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.album-view__description {
  margin: var(--space-2) 0 0;
  color: var(--color-text-muted);
  max-width: 40rem;
}

.album-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.album-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.album-view__footer {
  display: flex;
  justify-content: center;
}
</style>
