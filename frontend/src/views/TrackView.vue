<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterLink } from "vue-router";
import {
  getTrack,
  deleteTrack as deleteTrackApi,
  type TrackResponse,
} from "@/api/tracks";
import { getArtist, type ArtistResponse } from "@/api/artists";
import { getAlbum, type AlbumResponse } from "@/api/albums";
import { getApiErrorMessage } from "@/api/client";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import { useEntityMeta } from "@/composables/useEntityMeta";
import { useOwnership } from "@/composables/useOwnership";
import { useShareDialog } from "@/composables/useShareDialog";
import { useEntityDelete } from "@/composables/useEntityDelete";
import AddToCollectionDialog from "@/components/library/AddToCollectionDialog.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";
import { toQueueTrack } from "@/player/enrich";
import { formatTime } from "@/utils/time";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";

const { t } = useI18n();
const route = useRoute();
const player = usePlayerStore();
const authStore = useAuthStore();
const trackId = computed(() => String(route.params.id));

const addDialogOpen = ref(false);
const addDialogMode = ref<"library" | "playlist">("library");

function openAddDialog(mode: "library" | "playlist") {
  addDialogMode.value = mode;
  addDialogOpen.value = true;
}

const track = ref<TrackResponse | null>(null);
const artist = ref<ArtistResponse | null>(null);
const album = ref<AlbumResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const queueTrack = computed(() => {
  if (!track.value) return null;
  return toQueueTrack(track.value, {
    artist_name: artist.value?.name ?? "",
    album_title: album.value?.title,
    artwork_url: album.value?.cover_url ?? undefined,
  });
});

const { ownerName, visibilityText } = useEntityMeta(track);
const { isOwner } = useOwnership(computed(() => track.value?.owner_id ?? null));

const isPublic = computed(() => track.value?.visibility === "public");

const deleteTrack = useEntityDelete({
  delete: deleteTrackApi,
  entity: t("browse.entities.track"),
  redirectTo: "/tracks",
  allowRecursive: false,
  getName: () => track.value?.title ?? "",
  getOwnerId: () => track.value?.owner_id,
});

const {
  modalOpen: deleteModalOpen,
  modalTitle: deleteModalTitle,
  modalMessage: deleteModalMessage,
  modalLoading: deleteModalLoading,
  canDelete: canDeleteTrack,
} = deleteTrack;

const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

const durationText = computed(() =>
  track.value?.duration != null ? formatTime(track.value.duration) : "—",
);

const actions = computed(() => [
  {
    key: "play",
    label: t("common.play"),
    icon: "play",
    visible: true,
    disabled: !queueTrack.value,
  },
  {
    key: "share",
    label: t("common.share"),
    icon: "share-nodes",
    variant: "secondary" as const,
    visible: isOwner.value || isPublic.value,
  },
  {
    key: "add-to-library",
    label: t("browse.addToCollection.addToLibrary"),
    icon: "folder-plus",
    variant: "secondary" as const,
    visible: authStore.isAuthenticated,
  },
  {
    key: "add-to-playlist",
    label: t("browse.addToCollection.addToPlaylist"),
    icon: "list",
    variant: "secondary" as const,
    visible: authStore.isAuthenticated,
  },
  {
    key: "delete",
    label: t("common.delete"),
    icon: "trash",
    variant: "danger" as const,
    visible: canDeleteTrack.value,
  },
]);

function onAction(key: string) {
  if (!track.value) return;
  switch (key) {
    case "play":
      play();
      break;
    case "share":
      openShare(
        "track",
        track.value.id,
        track.value.title,
        track.value.owner_id,
        track.value.visibility,
      );
      break;
    case "add-to-library":
      openAddDialog("library");
      break;
    case "add-to-playlist":
      openAddDialog("playlist");
      break;
    case "delete":
      deleteTrack.open(track.value.id);
      break;
  }
}

async function loadTrack() {
  loading.value = true;
  error.value = null;
  track.value = null;
  artist.value = null;
  album.value = null;

  try {
    track.value = await getTrack(trackId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
    loading.value = false;
    return;
  }

  if (track.value.artist_id) {
    try {
      artist.value = await getArtist(track.value.artist_id);
    } catch {
      artist.value = null;
    }
  }

  if (track.value.album_id) {
    try {
      album.value = await getAlbum(track.value.album_id);
    } catch {
      album.value = null;
    }
  }

  loading.value = false;
}

function play() {
  if (!queueTrack.value) return;
  player.playTrack(queueTrack.value);
}

onMounted(() => loadTrack());
watch(
  () => route.params.id,
  () => loadTrack(),
);
</script>

<template>
  <div class="track-view">
    <div v-if="loading && !track" class="track-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="track-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="loadTrack">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else-if="track">
      <div class="track-view__header">
        <div class="track-view__info">
          <AppPageTitle class="track-view__title" icon="music">{{
            track.title
          }}</AppPageTitle>

          <div class="track-view__meta">
            <span v-if="artist" class="track-view__meta-item">
              <RouterLink
                :to="`/artists/${track.artist_id}`"
                class="track-view__link"
              >
                {{ artist.name }}
              </RouterLink>
            </span>
            <span v-else-if="track.artist_id" class="track-view__meta-item">
              {{ t("browse.entities.artist") }}
            </span>

            <span v-if="album" class="track-view__meta-item">
              <RouterLink
                :to="`/albums/${track.album_id}`"
                class="track-view__link"
              >
                {{ album.title }}
              </RouterLink>
            </span>

            <span v-if="track.genre" class="track-view__meta-item">
              {{ t("browse.detail.genre") }} {{ track.genre }}
            </span>

            <span class="track-view__meta-item">
              {{ t("browse.detail.duration") }} {{ durationText }}
            </span>

            <span v-if="track.track_number" class="track-view__meta-item">
              {{ t("browse.detail.trackNumber") }} {{ track.track_number }}
            </span>

            <span v-if="track.disc_number" class="track-view__meta-item">
              {{ t("browse.detail.discNumber") }} {{ track.disc_number }}
            </span>

            <span class="track-view__meta-item">
              {{ t("browse.detail.visibility") }} {{ visibilityText }}
            </span>

            <span v-if="ownerName" class="track-view__meta-item">
              {{ t("browse.detail.owner") }} {{ ownerName }}
            </span>
          </div>
        </div>

        <EntityActions
          class="track-view__header-actions"
          :actions="actions"
          size="lg"
          @select="onAction"
        />
      </div>
    </template>

    <AddToCollectionDialog
      v-if="track"
      :open="addDialogOpen"
      :mode="addDialogMode"
      item-type="track"
      :item-id="track.id"
      :item-name="track.title"
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
      :allow-recursive="false"
      :loading="deleteModalLoading"
      @close="deleteTrack.close"
      @confirm="deleteTrack.confirm"
    />
  </div>
</template>

<style scoped>
.track-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.track-view__skeleton {
  min-height: 16rem;
}

.track-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.track-view__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.track-view__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  flex: 1;
  min-width: 16rem;
}

.track-view__title {
  margin: 0;
  font-size: 2rem;
}

.track-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.track-view__meta-item {
  display: inline-flex;
  align-items: center;
  word-break: break-word;
}

.track-view__link {
  color: var(--color-accent-contrast);
  text-decoration: none;
}

.track-view__link:hover {
  text-decoration: underline;
}
</style>
