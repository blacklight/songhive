<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import {
  getPlaylist,
  listPlaylistTracks,
  deletePlaylist as deletePlaylistApi,
  type PlaylistResponse,
} from "@/api/playlists";
import type { TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useEntityMeta } from "@/composables/useEntityMeta";
import { useOwnership } from "@/composables/useOwnership";
import { useShareDialog } from "@/composables/useShareDialog";
import { useEntityDelete } from "@/composables/useEntityDelete";
import { useAuthStore } from "@/stores/auth";
import type { QueueTrack } from "@/player/types";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";

const { t } = useI18n();
const route = useRoute();
const authStore = useAuthStore();
const playlistId = computed(() => String(route.params.id));

const playlist = ref<PlaylistResponse | null>(null);
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
  listPlaylistTracks(playlistId.value, {
    limit: params.limit,
    offset: params.offset,
    include: "artist,album",
  }),
);

const { ownerName, ownerAvatarUrl, visibilityText, visibilityIcon } =
  useEntityMeta(playlist);

const { isOwner } = useOwnership(
  computed(() => playlist.value?.owner_id ?? null),
);

const isPublic = computed(() => playlist.value?.visibility === "public");

const deletePlaylist = useEntityDelete({
  delete: deletePlaylistApi,
  entity: t("browse.entities.playlist"),
  redirectTo: "/playlists",
  allowRecursive: true,
  recursiveLabel: t("browse.delete.recursive", {
    contents: t("browse.entities.tracks"),
  }),
  getName: () => playlist.value?.name ?? "",
  getOwnerId: () => playlist.value?.owner_id,
});

const {
  modalOpen: deleteModalOpen,
  modalTitle: deleteModalTitle,
  modalMessage: deleteModalMessage,
  modalLoading: deleteModalLoading,
  allowRecursive: deleteAllowRecursive,
  recursiveLabel: deleteRecursiveLabel,
  canDelete: canDeletePlaylist,
} = deletePlaylist;

const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

const canRemove = computed(() => isOwner.value || authStore.isAdmin);

const removableFrom = computed(() => {
  if (!playlist.value) return undefined;
  return {
    type: "playlist" as const,
    id: playlist.value.id,
    canRemove: canRemove.value,
    name: playlist.value.name,
  };
});

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
    key: "delete",
    label: t("common.delete"),
    icon: "trash",
    variant: "danger" as const,
    visible: canDeletePlaylist.value,
  },
]);

function onAction(key: string) {
  if (!playlist.value) return;
  switch (key) {
    case "share":
      openShare(
        "playlist",
        playlist.value.id,
        playlist.value.name,
        playlist.value.owner_id,
        playlist.value.visibility,
      );
      break;
    case "delete":
      deletePlaylist.open(playlist.value.id);
      break;
  }
}

async function loadPlaylist() {
  loading.value = true;
  error.value = null;
  try {
    playlist.value = await getPlaylist(playlistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  playlist.value = null;
  error.value = null;
  await loadPlaylist();
  if (!playlist.value) return;
  await loadTracks(true);
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="playlist-view">
    <div v-if="loading && !playlist" class="playlist-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="playlist-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else-if="playlist">
      <div class="playlist-view__header">
        <AppPageTitle class="playlist-view__name" icon="list">{{
          playlist.name
        }}</AppPageTitle>

        <p v-if="playlist.description" class="playlist-view__description">
          {{ playlist.description }}
        </p>

        <div class="playlist-view__meta">
          <span
            v-if="ownerName"
            :title="ownerName"
            class="playlist-view__owner"
          >
            <AppAvatar
              v-if="ownerAvatarUrl"
              :src="ownerAvatarUrl"
              :name="ownerName"
              width="16px"
            />
            {{ ownerName }}
          </span>
          <span :title="visibilityText" class="playlist-view__visibility">
            <i :class="visibilityIcon" />
          </span>
        </div>

        <EntityActions
          class="playlist-view__header-actions"
          :actions="actions"
          @select="onAction"
        />
      </div>

      <section
        class="playlist-view__section"
        aria-labelledby="playlist-tracks-heading"
      >
        <AppPageTitle
          id="playlist-tracks-heading"
          :level="2"
          class="playlist-view__section-title"
          icon="music"
        >
          {{ t("browse.detail.tracks") }}
        </AppPageTitle>

        <div
          v-if="tracksError"
          class="playlist-view__section-error"
          role="alert"
        >
          <span>{{ tracksError }}</span>
          <AppButton size="sm" icon="rotate-right" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :context="playlist.name"
          :removable-from="removableFrom"
          :empty-label="t('browse.playlist.empty')"
          :deletable="true"
          @share="onTrackShare"
          @removed="onTracksRemoved"
        />

        <div class="playlist-view__footer">
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
        @close="deletePlaylist.close"
        @confirm="deletePlaylist.confirm"
      />
    </template>
  </div>
</template>

<style scoped>
.playlist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.playlist-view__skeleton {
  min-height: 16rem;
}

.playlist-view__error,
.playlist-view__section-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.playlist-view__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.playlist-view__name {
  margin: 0;
  font-size: 2rem;
}

.playlist-view__description {
  margin: 0;
  color: var(--color-text-muted);
  max-width: 40rem;
  word-break: break-word;
}

.playlist-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
  word-break: break-word;
  align-items: center;
}

.playlist-view__owner {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.playlist-view__visibility {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

:deep(.playlist-view__owner img) {
  margin: 0;
}

.playlist-view__header-actions {
  margin-top: var(--space-2);
}

.playlist-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.playlist-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.playlist-view__footer {
  display: flex;
  justify-content: center;
}

:deep(.track-list) {
  margin-top: -2.5rem;
}
</style>
