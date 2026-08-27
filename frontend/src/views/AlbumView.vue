<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter, RouterLink } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import {
  getAlbum,
  deleteAlbum as deleteAlbumApi,
  enrichAlbum,
  type AlbumResponse,
} from "@/api/albums";
import { getArtist, type ArtistResponse } from "@/api/artists";
import { listTracks, type TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { useEntityMeta } from "@/composables/useEntityMeta";
import { useOwnership } from "@/composables/useOwnership";
import { useShareDialog } from "@/composables/useShareDialog";
import { useEntityDelete } from "@/composables/useEntityDelete";
import type { QueueTrack } from "@/player/types";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";
import AddToCollectionDialog from "@/components/library/AddToCollectionDialog.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const toastStore = useToastStore();
const albumId = computed(() => String(route.params.id));

const addDialogOpen = ref(false);
const addDialogMode = ref<"library" | "playlist">("library");

function openAddDialog(mode: "library" | "playlist") {
  addDialogMode.value = mode;
  addDialogOpen.value = true;
}

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
  refresh: refreshTracks,
} = useEntityList<TrackResponse>((params: EntityListParams) =>
  listTracks({
    q: params.q,
    album_id: albumId.value,
    limit: params.limit,
    offset: params.offset,
    include: "artist,album",
  }),
);

const artistName = computed(() => artist.value?.name ?? "");

const { ownerName, ownerAvatarUrl, visibilityText, visibilityIcon } =
  useEntityMeta(album);
const { isOwner } = useOwnership(computed(() => album.value?.owner_id ?? null));

const isPublic = computed(() => album.value?.visibility === "public");

const deleteAlbum = useEntityDelete({
  delete: deleteAlbumApi,
  entity: t("browse.entities.album"),
  redirectTo: "/albums",
  allowRecursive: true,
  recursiveLabel: t("browse.delete.recursive", {
    contents: t("browse.entities.tracks"),
  }),
  getName: () => album.value?.title ?? "",
  getOwnerId: () => album.value?.owner_id,
});

const {
  modalOpen: deleteModalOpen,
  modalTitle: deleteModalTitle,
  modalMessage: deleteModalMessage,
  modalLoading: deleteModalLoading,
  allowRecursive: deleteAllowRecursive,
  recursiveLabel: deleteRecursiveLabel,
  canDelete: canDeleteAlbum,
} = deleteAlbum;

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

async function onTracksRemoved() {
  await refreshTracks();
}

const actions = computed(() => [
  {
    key: "share",
    label: t("common.share"),
    icon: "share-nodes",
    variant: "secondary" as const,
    visible: isOwner.value || isPublic.value,
  },
  {
    key: "edit",
    label: t("common.edit"),
    icon: "pen-to-square",
    variant: "secondary" as const,
    visible: isOwner.value,
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
    key: "enrich",
    label: t("browse.enrich.metadata"),
    icon: "wand-magic-sparkles",
    visible: canDeleteAlbum.value,
  },
  {
    key: "delete",
    label: t("common.delete"),
    icon: "trash",
    variant: "danger" as const,
    visible: canDeleteAlbum.value,
  },
]);

async function onAction(key: string) {
  if (!album.value) return;
  switch (key) {
    case "share":
      openShare(
        "album",
        album.value.id,
        album.value.title,
        album.value.owner_id,
        album.value.visibility,
      );
      break;
    case "edit":
      await router.push({
        name: "albumEdit",
        params: { id: album.value.id },
      });
      break;
    case "add-to-library":
      openAddDialog("library");
      break;
    case "add-to-playlist":
      openAddDialog("playlist");
      break;
    case "enrich":
      onEnrichAlbum();
      break;
    case "delete":
      deleteAlbum.open(album.value.id);
      break;
  }
}

async function onEnrichAlbum() {
  try {
    const response = await enrichAlbum(albumId.value);
    toastStore.push({
      type: "success",
      message: t("browse.enrich.albumSuccess", { count: response.enqueued }),
    });
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("browse.enrich.error", { message: getApiErrorMessage(err) }),
    });
  }
}

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
      <AppButton size="sm" icon="rotate-right" @click="load">{{
        t("common.retry")
      }}</AppButton>
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
          <AppPageTitle class="album-view__title" icon="compact-disc">{{
            album.title
          }}</AppPageTitle>

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
            <span class="album-view__meta-item">
              <span v-if="album.release_year" class="album-view__meta-item">
                <i class="fa-solid fa-calendar" />
                {{ album.release_year }}
              </span>
            </span>
            <span class="album-view__meta-item">
              <span
                v-if="ownerName"
                :title="ownerName"
                class="album-view__owner"
              >
                <AppAvatar
                  v-if="ownerAvatarUrl"
                  :src="ownerAvatarUrl"
                  :name="ownerName"
                  width="16px"
                />
                {{ ownerName }}
              </span>
              <span :title="visibilityText" class="album-view__visibility">
                <i :class="visibilityIcon" />
              </span>
            </span>
          </div>

          <p v-if="album.description" class="album-view__description">
            {{ album.description }}
          </p>

          <EntityActions
            class="album-view__header-actions"
            :actions="actions"
            @select="onAction"
          />
        </div>
      </div>

      <section
        class="album-view__section"
        aria-labelledby="album-tracks-heading"
      >
        <AppPageTitle
          id="album-tracks-heading"
          :level="2"
          class="album-view__section-title"
          icon="music"
        >
          {{ t("browse.detail.tracks") }}
        </AppPageTitle>

        <div v-if="tracksError" class="album-view__section-error" role="alert">
          <span>{{ tracksError }}</span>
          <AppButton size="sm" icon="rotate-right" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :context="artistName"
          :show-artwork="true"
          :deletable="true"
          @share="onTrackShare"
          @removed="onTracksRemoved"
        />

        <div class="album-view__footer">
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
      v-if="album"
      :open="addDialogOpen"
      :mode="addDialogMode"
      item-type="album"
      :item-id="album.id"
      :item-name="album.title"
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
      @close="deleteAlbum.close"
      @confirm="deleteAlbum.confirm"
    />
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
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: 1.125rem;
  word-break: break-word;
}

.album-view__artist:hover {
  text-decoration: underline;
}

.album-view__meta {
  display: flex;
  flex-wrap: wrap;
  flex-direction: column;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: 0.875rem;
  word-break: break-word;
}

.album-view__meta-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.album-view__owner {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.album-view__visibility {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

:deep(.album-view__owner img) {
  margin: 0;
}

:deep(.album-view__owner .app-avatar--initials) {
  font-size: 0.55rem;
}

.album-view__description {
  margin: var(--space-2) 0 0;
  color: var(--color-text-muted);
  max-width: 40rem;
  word-break: break-word;
}

.album-view__header-actions {
  margin-top: var(--space-2);
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
