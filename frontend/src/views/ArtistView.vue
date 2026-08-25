<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import {
  getArtist,
  deleteArtist as deleteArtistApi,
  type ArtistResponse,
} from "@/api/artists";
import { listAlbums, type AlbumResponse } from "@/api/albums";
import { listTracks, type TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import type { TrackEnrich } from "@/player/enrich";
import { useShareDialog } from "@/composables/useShareDialog";
import { useEntityDelete } from "@/composables/useEntityDelete";
import type { QueueTrack } from "@/player/types";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AlbumCard from "@/components/library/AlbumCard.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";
import AddToCollectionDialog from "@/components/library/AddToCollectionDialog.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";

const { t } = useI18n();
const route = useRoute();
const authStore = useAuthStore();
const artistId = computed(() => String(route.params.id));

const addDialogOpen = ref(false);
const addDialogMode = ref<"library" | "playlist">("library");

function openAddDialog(mode: "library" | "playlist") {
  addDialogMode.value = mode;
  addDialogOpen.value = true;
}

const artist = ref<ArtistResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const allAlbums = ref<AlbumResponse[]>([]);

const {
  items: albums,
  loading: albumsLoading,
  error: albumsError,
  hasMore: albumsHasMore,
  load: loadAlbums,
  loadMore: loadMoreAlbums,
  retry: retryAlbums,
} = useEntityList<AlbumResponse>((params: EntityListParams) =>
  listAlbums({
    q: params.q,
    artist_id: artistId.value,
    limit: params.limit,
    offset: params.offset,
  }),
);

const {
  items: tracks,
  loading: tracksLoading,
  error: tracksError,
  hasMore: tracksHasMore,
  load: loadTracks,
  loadMore: loadMoreTracks,
  retry: retryTracks,
  refresh: refreshTracks,
} = useEntityList<TrackResponse>((params: EntityListParams) =>
  listTracks({
    q: params.q,
    artist_id: artistId.value,
    limit: params.limit,
    offset: params.offset,
  }),
);

const allAlbumsMap = computed(
  () => new Map(allAlbums.value.map((album) => [album.id, album])),
);

const trackEnrich = computed<Map<string, TrackEnrich>>(() => {
  const map = new Map<string, TrackEnrich>();
  for (const track of tracks.value) {
    const album = track.album_id
      ? allAlbumsMap.value.get(track.album_id)
      : null;
    map.set(track.id, {
      artist_name: artist.value?.name ?? "",
      album_title: album?.title,
      artwork_url: album?.cover_url ?? undefined,
    });
  }
  return map;
});

const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

const deleteArtist = useEntityDelete({
  delete: deleteArtistApi,
  entity: t("browse.entities.artist"),
  redirectTo: "/artists",
  allowRecursive: true,
  recursiveLabel: t("browse.delete.recursive", {
    contents: `${t("browse.entities.albums")} / ${t("browse.entities.tracks")}`,
  }),
  getName: () => artist.value?.name ?? "",
  getOwnerId: () => null,
});

const {
  modalOpen: deleteModalOpen,
  modalTitle: deleteModalTitle,
  modalMessage: deleteModalMessage,
  modalLoading: deleteModalLoading,
  allowRecursive: deleteAllowRecursive,
  recursiveLabel: deleteRecursiveLabel,
  canDelete: canDeleteArtist,
} = deleteArtist;

function onTrackShare(track: QueueTrack) {
  openShare("track", track.id, track.title, track.owner_id ?? null);
}

async function onTracksRemoved() {
  await refreshTracks();
}

async function loadArtist() {
  loading.value = true;
  error.value = null;
  try {
    artist.value = await getArtist(artistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function loadAllAlbums() {
  const collected: AlbumResponse[] = [];
  const limit = 100;
  let offset = 0;
  let hasMore = true;
  while (hasMore) {
    try {
      const page = await listAlbums({
        q: "",
        artist_id: artistId.value,
        limit,
        offset,
      });
      collected.push(...page);
      hasMore = page.length === limit;
      if (hasMore) offset += limit;
    } catch {
      // Best-effort enrichment; the paginated discography grid still works.
      hasMore = false;
    }
  }
  allAlbums.value = collected;
}

async function load() {
  artist.value = null;
  allAlbums.value = [];
  error.value = null;
  await loadArtist();
  if (!artist.value) return;
  await Promise.all([loadAllAlbums(), loadAlbums(true), loadTracks(true)]);
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="artist-view">
    <div v-if="loading && !artist" class="artist-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="artist-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else-if="artist">
      <div class="artist-view__header">
        <AppAvatar
          :src="artist.image_url ?? undefined"
          :name="artist.name"
          size="lg"
          class="artist-view__avatar"
        />
        <div class="artist-view__info">
          <AppPageTitle class="artist-view__name" icon="users">{{
            artist.name
          }}</AppPageTitle>
          <p v-if="artist.bio" class="artist-view__bio">{{ artist.bio }}</p>
          <div class="artist-view__header-actions">
            <AppButton
              v-if="authStore.isAuthenticated"
              size="sm"
              icon="folder-plus"
              @click="openAddDialog('library')"
            >
              {{ t("browse.addToCollection.addToLibrary") }}
            </AppButton>
            <AppButton
              v-if="authStore.isAuthenticated"
              size="sm"
              icon="list"
              @click="openAddDialog('playlist')"
            >
              {{ t("browse.addToCollection.addToPlaylist") }}
            </AppButton>
            <AppButton
              v-if="canDeleteArtist"
              size="sm"
              variant="danger"
              icon="trash"
              @click="artist && deleteArtist.open(artist.id)"
            >
              {{ t("common.delete") }}
            </AppButton>
          </div>
        </div>
      </div>

      <section
        class="artist-view__section"
        aria-labelledby="artist-albums-heading"
      >
        <AppPageTitle
          id="artist-albums-heading"
          :level="2"
          class="artist-view__section-title"
          icon="compact-disc"
        >
          {{ t("browse.detail.discography") }}
        </AppPageTitle>

        <div
          v-if="albumsLoading && albums.length === 0"
          class="artist-view__grid artist-view__grid--skeleton"
        >
          <SkeletonLoader v-for="i in 6" :key="i" variant="card" />
        </div>

        <div
          v-else-if="albumsError"
          class="artist-view__section-error"
          role="alert"
        >
          <span>{{ albumsError }}</span>
          <AppButton size="sm" icon="rotate-right" @click="retryAlbums">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <div v-else-if="albums.length === 0" class="artist-view__empty">
          {{ t("browse.list.empty", { entity: t("browse.entities.albums") }) }}
        </div>

        <div v-else class="artist-view__grid">
          <AlbumCard
            v-for="album in albums"
            :key="album.id"
            :album="album"
            :artist-name="artist.name"
          />
        </div>

        <div class="artist-view__footer">
          <AppButton
            v-if="albumsHasMore"
            icon="chevron-down"
            variant="secondary"
            :loading="albumsLoading"
            :disabled="albumsLoading"
            @click="loadMoreAlbums"
          >
            {{ t("browse.list.loadMore") }}
          </AppButton>
        </div>
      </section>

      <section
        class="artist-view__section"
        aria-labelledby="artist-tracks-heading"
      >
        <AppPageTitle
          id="artist-tracks-heading"
          :level="2"
          class="artist-view__section-title"
          icon="music"
        >
          {{ t("browse.detail.tracks") }}
        </AppPageTitle>

        <div v-if="tracksError" class="artist-view__section-error" role="alert">
          <span>{{ tracksError }}</span>
          <AppButton size="sm" icon="rotate-right" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :context="artist.name"
          :enrich="trackEnrich"
          :deletable="true"
          @share="onTrackShare"
          @removed="onTracksRemoved"
        />

        <div class="artist-view__footer">
          <AppButton
            v-if="tracksHasMore"
            icon="chevron-down"
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

    <AddToCollectionDialog
      v-if="artist"
      :open="addDialogOpen"
      :mode="addDialogMode"
      item-type="artist"
      :item-id="artist.id"
      :item-name="artist.name"
      @close="addDialogOpen = false"
    />

    <ShareDialog
      v-if="shareTarget"
      :open="shareOpen"
      :item-type="shareTarget.itemType"
      :item-id="shareTarget.itemId"
      :title="shareTarget.title"
      :owner-id="shareTarget.ownerId"
      @close="closeShare"
    />

    <DeleteModal
      :open="deleteModalOpen"
      :title="deleteModalTitle"
      :message="deleteModalMessage"
      :allow-recursive="deleteAllowRecursive"
      :recursive-label="deleteRecursiveLabel"
      :loading="deleteModalLoading"
      @close="deleteArtist.close"
      @confirm="deleteArtist.confirm"
    />
  </div>
</template>

<style scoped>
.artist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.artist-view__skeleton {
  min-height: 16rem;
}

.artist-view__error,
.artist-view__section-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.artist-view__header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.artist-view__avatar {
  width: 8rem;
  height: 8rem;
}

.artist-view__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.artist-view__name {
  margin: 0;
  font-size: 2rem;
}

.artist-view__bio {
  margin: 0;
  color: var(--color-text-muted);
  max-width: 40rem;
}

.artist-view__header-actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  margin-top: var(--space-2);
}

.artist-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artist-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.artist-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}

.artist-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.artist-view__footer {
  display: flex;
  justify-content: center;
}
</style>
