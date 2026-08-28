<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
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
import { useShareDialog } from "@/composables/useShareDialog";
import { useEntityDelete } from "@/composables/useEntityDelete";
import { useCanManage } from "@/composables/useCanManage";
import type { QueueTrack } from "@/player/types";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AlbumCard from "@/components/library/AlbumCard.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";
import AddToCollectionDialog from "@/components/library/AddToCollectionDialog.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";
import SortControl from "@/components/ui/SortControl.vue";
import HashtagList from "@/components/hashtags/HashtagList.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
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

const {
  items: albums,
  loading: albumsLoading,
  error: albumsError,
  hasMore: albumsHasMore,
  sortBy: albumSortBy,
  sortDir: albumSortDir,
  load: loadAlbums,
  loadMore: loadMoreAlbums,
  setSort: setAlbumSort,
  retry: retryAlbums,
} = useEntityList<AlbumResponse>(
  (params: EntityListParams) =>
    listAlbums({
      q: params.q,
      artist_id: artistId.value,
      limit: params.limit,
      offset: params.offset,
      sort_by: params.sort_by,
      sort_dir: params.sort_dir,
    }),
  {
    defaultSortBy: "title",
    syncQuery: true,
    queryKey: "albums",
  },
);

const {
  items: tracks,
  loading: tracksLoading,
  error: tracksError,
  hasMore: tracksHasMore,
  sortBy: trackSortBy,
  sortDir: trackSortDir,
  load: loadTracks,
  loadMore: loadMoreTracks,
  setSort: setTrackSort,
  retry: retryTracks,
  refresh: refreshTracks,
} = useEntityList<TrackResponse>(
  (params: EntityListParams) =>
    listTracks({
      q: params.q,
      artist_id: artistId.value,
      limit: params.limit,
      offset: params.offset,
      sort_by: params.sort_by,
      sort_dir: params.sort_dir,
      include: "artist,album",
    }),
  {
    defaultSortBy: "created_at",
    defaultSortDir: "desc",
    syncQuery: true,
    queryKey: "tracks",
  },
);

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

const { canManage } = useCanManage();

function onTrackShare(track: QueueTrack) {
  openShare(
    "track",
    track.id,
    track.title,
    track.owner_id ?? null,
    track.visibility,
  );
}

async function onTracksRemoved() {
  await refreshTracks();
}

const albumSortOptions = computed(() => [
  { value: "title", label: t("sort.fields.title") },
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
  { value: "release_year", label: t("sort.fields.release_year") },
]);

const trackSortOptions = computed(() => [
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "title", label: t("sort.fields.title") },
  { value: "album_title", label: t("sort.fields.album_title") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
  { value: "release_year", label: t("sort.fields.release_year") },
]);

function onAlbumSort(field: string, direction: "asc" | "desc") {
  void setAlbumSort(field, direction);
}

function onTrackSort(field: string, direction: "asc" | "desc") {
  void setTrackSort(field, direction);
}

const actions = computed(() => [
  {
    key: "share",
    label: t("common.share"),
    icon: "share-nodes",
    variant: "secondary" as const,
    visible: true,
  },
  {
    key: "edit",
    label: t("common.edit"),
    icon: "pen-to-square",
    variant: "secondary" as const,
    visible: canManage.value,
  },
  {
    key: "add-to-library",
    label: t("browse.addToCollection.addToLibrary"),
    icon: "folder-plus",
    visible: authStore.isAuthenticated,
  },
  {
    key: "add-to-playlist",
    label: t("browse.addToCollection.addToPlaylist"),
    icon: "list",
    visible: authStore.isAuthenticated,
  },
  {
    key: "delete",
    label: t("common.delete"),
    icon: "trash",
    variant: "danger" as const,
    visible: canDeleteArtist.value,
  },
]);

async function onAction(key: string) {
  if (!artist.value) return;
  switch (key) {
    case "share":
      openShare("artist", artist.value.id, artist.value.name, null, null);
      break;
    case "edit":
      await router.push({
        name: "artistEdit",
        params: { id: artist.value.id },
      });
      break;
    case "add-to-library":
      openAddDialog("library");
      break;
    case "add-to-playlist":
      openAddDialog("playlist");
      break;
    case "delete":
      deleteArtist.open(artist.value.id);
      break;
  }
}

async function loadArtist() {
  loading.value = true;
  error.value = null;
  try {
    artist.value = await getArtist(artistId.value, { include: "hashtags" });
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  artist.value = null;
  error.value = null;
  await loadArtist();
  if (!artist.value) return;
  await Promise.all([loadAlbums(true), loadTracks(true)]);
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
          <div v-if="artist.hashtags?.length" class="artist-view__hashtags">
            <HashtagList :hashtags="artist.hashtags" />
          </div>
        </div>
        <EntityActions
          class="artist-view__header-actions"
          :actions="actions"
          @select="onAction"
        />
      </div>

      <section
        class="artist-view__section"
        aria-labelledby="artist-albums-heading"
      >
        <div class="artist-view__section-header">
          <AppPageTitle
            id="artist-albums-heading"
            :level="2"
            class="artist-view__section-title"
            icon="compact-disc"
          >
            {{ t("browse.detail.discography") }}
          </AppPageTitle>

          <SortControl
            :model-value="albumSortBy"
            :direction="albumSortDir"
            :options="albumSortOptions"
            @update:model-value="(field) => onAlbumSort(field, albumSortDir)"
            @update:direction="(dir) => onAlbumSort(albumSortBy, dir)"
          />
        </div>

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
        <div class="artist-view__section-header">
          <AppPageTitle
            id="artist-tracks-heading"
            :level="2"
            class="artist-view__section-title"
            icon="music"
          >
            {{ t("browse.detail.tracks") }}
          </AppPageTitle>

          <SortControl
            :model-value="trackSortBy"
            :direction="trackSortDir"
            :options="trackSortOptions"
            @update:model-value="(field) => onTrackSort(field, trackSortDir)"
            @update:direction="(dir) => onTrackSort(trackSortBy, dir)"
          />
        </div>

        <div v-if="tracksError" class="artist-view__section-error" role="alert">
          <span>{{ tracksError }}</span>
          <AppButton size="sm" icon="rotate-right" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :auto-scroll="false"
          :context="artist.name"
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
      :visibility="shareTarget.visibility"
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
  display: grid;
  grid-template-columns: auto 1fr;
  grid-template-areas:
    "avatar name"
    "avatar actions";
  gap: var(--space-4);
  align-items: center;
}

.artist-view__avatar {
  grid-area: avatar;
  width: 8rem;
  height: 8rem;
  flex-shrink: 0;
}

.artist-view__info {
  grid-area: name;
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
  word-break: break-word;
}

.artist-view__hashtags {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.artist-view__header-actions {
  grid-area: actions;
}

@media (max-width: 767px) {
  .artist-view__header {
    grid-template-areas:
      "avatar name"
      "actions actions";
  }

  .artist-view__avatar {
    width: clamp(4rem, 20vw, 8rem);
    height: clamp(4rem, 20vw, 8rem);
  }

  .artist-view__name {
    font-size: clamp(1.5rem, 7vw, 2rem);
  }
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

.artist-view__section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
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
