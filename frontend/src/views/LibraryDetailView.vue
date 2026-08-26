<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import {
  getLibrary,
  listLibraryTracks,
  deleteLibrary as deleteLibraryApi,
  type LibraryResponse,
} from "@/api/libraries";
import type { TrackResponse } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useEntityMeta } from "@/composables/useEntityMeta";
import { useOwnership } from "@/composables/useOwnership";
import { useShareDialog } from "@/composables/useShareDialog";
import { useEntityDelete } from "@/composables/useEntityDelete";
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
const libraryId = computed(() => String(route.params.id));

const library = ref<LibraryResponse | null>(null);
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
  listLibraryTracks(libraryId.value, {
    limit: params.limit,
    offset: params.offset,
    include: "artist,album",
  }),
);

const { ownerName, ownerAvatarUrl, visibilityText, visibilityIcon } =
  useEntityMeta(library);

const { isOwner } = useOwnership(
  computed(() => library.value?.owner_id ?? null),
);

const isPublic = computed(() => library.value?.visibility === "public");

const deleteLibrary = useEntityDelete({
  delete: deleteLibraryApi,
  entity: t("browse.entities.library"),
  redirectTo: "/libraries",
  allowRecursive: true,
  recursiveLabel: t("browse.delete.recursive", {
    contents: t("browse.entities.tracks"),
  }),
  getName: () => library.value?.name ?? "",
  getOwnerId: () => library.value?.owner_id,
});

const {
  modalOpen: deleteModalOpen,
  modalTitle: deleteModalTitle,
  modalMessage: deleteModalMessage,
  modalLoading: deleteModalLoading,
  allowRecursive: deleteAllowRecursive,
  recursiveLabel: deleteRecursiveLabel,
  canDelete: canDeleteLibrary,
} = deleteLibrary;

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

const removableFrom = computed(() => {
  if (!library.value) return undefined;
  return {
    type: "library" as const,
    id: library.value.id,
    canRemove: library.value.can_write,
    name: library.value.name,
  };
});

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
    visible: canDeleteLibrary.value,
  },
]);

function onAction(key: string) {
  if (!library.value) return;
  switch (key) {
    case "share":
      openShare(
        "library",
        library.value.id,
        library.value.name,
        library.value.owner_id,
        library.value.visibility,
      );
      break;
    case "delete":
      deleteLibrary.open(library.value.id);
      break;
  }
}

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
      <AppButton size="sm" icon="rotate-right" @click="load">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else-if="library">
      <div class="library-detail-view__header">
        <AppPageTitle class="library-detail-view__name" icon="folder-open">{{
          library.name
        }}</AppPageTitle>

        <p v-if="library.description" class="library-detail-view__description">
          {{ library.description }}
        </p>

        <div class="library-detail-view__meta">
          <span
            v-if="ownerName"
            :title="ownerName"
            class="library-detail-view__owner"
          >
            <AppAvatar
              v-if="ownerAvatarUrl"
              :src="ownerAvatarUrl"
              :name="ownerName"
              width="16px"
            />
            {{ ownerName }}
          </span>
          <span :title="visibilityText" class="library-detail-view__visibility">
            <i :class="visibilityIcon" />
          </span>
        </div>

        <EntityActions
          class="library-detail-view__header-actions"
          :actions="actions"
          @select="onAction"
        />
      </div>

      <section
        class="library-detail-view__section"
        aria-labelledby="library-tracks-heading"
      >
        <AppPageTitle
          id="library-tracks-heading"
          :level="2"
          class="library-detail-view__section-title"
          icon="music"
        >
          {{ t("browse.detail.tracks") }}
        </AppPageTitle>

        <div
          v-if="tracksError"
          class="library-detail-view__section-error"
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
          :context="library.name"
          :removable-from="removableFrom"
          :deletable="true"
          @share="onTrackShare"
          @removed="onTracksRemoved"
        />

        <div class="library-detail-view__footer">
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
        @close="deleteLibrary.close"
        @confirm="deleteLibrary.confirm"
      />
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
  word-break: break-word;
}

.library-detail-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
  word-break: break-word;
  align-items: center;
}

.library-detail-view__owner {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.library-detail-view__visibility {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

:deep(.library-detail-view__owner img) {
  margin: 0;
}

.library-detail-view__header-actions {
  margin-top: var(--space-2);
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

:deep(.track-list) {
  margin-top: -2.5rem;
}
</style>
