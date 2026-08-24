<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import {
  getLibrary,
  listLibraryTracks,
  type LibraryResponse,
} from "@/api/libraries";
import { getArtist, type ArtistResponse } from "@/api/artists";
import { getAlbum, type AlbumResponse } from "@/api/albums";
import type { TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useEntityMeta } from "@/composables/useEntityMeta";
import type { TrackEnrich } from "@/player/enrich";
import AppButton from "@/components/ui/AppButton.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";

const { t } = useI18n();
const route = useRoute();
const libraryId = computed(() => String(route.params.id));

const library = ref<LibraryResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const artistMap = shallowRef<Map<string, ArtistResponse | null>>(new Map());
const albumMap = shallowRef<Map<string, AlbumResponse | null>>(new Map());

const {
  items: tracks,
  loading: tracksLoading,
  error: tracksError,
  hasMore: tracksHasMore,
  load: loadTracks,
  loadMore: loadMoreTracks,
  retry: retryTracks,
} = useEntityList<TrackResponse>((params: EntityListParams) =>
  listLibraryTracks(libraryId.value, {
    limit: params.limit,
    offset: params.offset,
  }),
);

const { ownerName, visibilityText } = useEntityMeta(library);

const trackEnrich = computed<Map<string, TrackEnrich>>(() => {
  const map = new Map<string, TrackEnrich>();
  for (const track of tracks.value) {
    const artist = track.artist_id
      ? artistMap.value.get(track.artist_id)
      : null;
    const album = track.album_id ? albumMap.value.get(track.album_id) : null;
    map.set(track.id, {
      artist_name: artist?.name ?? library.value?.name ?? "",
      album_title: album?.title,
      artwork_url: album?.cover_url ?? undefined,
    });
  }
  return map;
});

async function loadTrackDetails() {
  if (tracks.value.length === 0) return;
  const missingArtistIds: string[] = [];
  const missingAlbumIds: string[] = [];
  for (const track of tracks.value) {
    if (track.artist_id && !artistMap.value.has(track.artist_id)) {
      missingArtistIds.push(track.artist_id);
    }
    if (track.album_id && !albumMap.value.has(track.album_id)) {
      missingAlbumIds.push(track.album_id);
    }
  }
  if (missingArtistIds.length === 0 && missingAlbumIds.length === 0) return;

  const [artists, albums] = await Promise.all([
    Promise.all(missingArtistIds.map((id) => getArtist(id).catch(() => null))),
    Promise.all(missingAlbumIds.map((id) => getAlbum(id).catch(() => null))),
  ]);

  const newArtistMap = new Map(artistMap.value);
  const newAlbumMap = new Map(albumMap.value);
  for (let i = 0; i < missingArtistIds.length; i++) {
    newArtistMap.set(missingArtistIds[i], artists[i]);
  }
  for (let i = 0; i < missingAlbumIds.length; i++) {
    newAlbumMap.set(missingAlbumIds[i], albums[i]);
  }
  artistMap.value = newArtistMap;
  albumMap.value = newAlbumMap;
}

watch(tracks, loadTrackDetails, { immediate: true });

async function loadLibrary() {
  loading.value = true;
  error.value = null;
  try {
    library.value = await getLibrary(libraryId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  library.value = null;
  error.value = null;
  artistMap.value = new Map();
  albumMap.value = new Map();
  await loadLibrary();
  if (!library.value) return;
  await loadTracks(true);
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="library-detail-view">
    <div v-if="loading && !library" class="library-detail-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="library-detail-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="load">{{ t("common.retry") }}</AppButton>
    </div>

    <template v-else-if="library">
      <div class="library-detail-view__header">
        <h1 class="library-detail-view__name">{{ library.name }}</h1>

        <p v-if="library.description" class="library-detail-view__description">
          {{ library.description }}
        </p>

        <div class="library-detail-view__meta">
          <span class="library-detail-view__meta-item">
            {{ t("browse.detail.visibility") }} {{ visibilityText }}
          </span>
          <span v-if="ownerName" class="library-detail-view__meta-item">
            {{ t("browse.detail.owner") }} {{ ownerName }}
          </span>
        </div>
      </div>

      <section
        class="library-detail-view__section"
        aria-labelledby="library-tracks-heading"
      >
        <h2
          id="library-tracks-heading"
          class="library-detail-view__section-title"
        >
          {{ t("browse.detail.tracks") }}
        </h2>

        <div
          v-if="tracksError"
          class="library-detail-view__section-error"
          role="alert"
        >
          <span>{{ tracksError }}</span>
          <AppButton size="sm" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :context="library.name"
          :enrich="trackEnrich"
        />

        <div class="library-detail-view__footer">
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
.library-detail-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.library-detail-view__skeleton {
  min-height: 16rem;
}

.library-detail-view__error,
.library-detail-view__section-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.library-detail-view__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.library-detail-view__name {
  margin: 0;
  font-size: 2rem;
}

.library-detail-view__description {
  margin: 0;
  color: var(--color-text-muted);
  max-width: 40rem;
}

.library-detail-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.library-detail-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.library-detail-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.library-detail-view__footer {
  display: flex;
  justify-content: center;
}
</style>
