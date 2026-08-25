<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import type { TrackResponse, QueueTrack } from "@/player/types";
import { toQueueTrack, type TrackEnrich } from "@/player/enrich";
import AppTable from "@/components/ui/AppTable.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import ContextMenu from "@/components/ui/ContextMenu.vue";
import AddToCollectionDialog from "@/components/library/AddToCollectionDialog.vue";
import { formatTime } from "@/utils/time";
import { getApiErrorMessage } from "@/api/client";
import { removeTracksFromLibrary } from "@/api/libraries";
import { removeTracksFromPlaylist } from "@/api/playlists";
import { deleteTrack } from "@/api/tracks";
import { canManageItem } from "@/composables/useCanManage";

export interface RemovableFrom {
  type: "library" | "playlist";
  id: string;
  canRemove: boolean;
  name: string;
}

export interface Props {
  tracks: TrackResponse[];
  context?: string;
  loading?: boolean;
  showArtwork?: boolean;
  enrich?: Map<string, TrackEnrich>;
  favoriteLabel?: string;
  emptyLabel?: string;
  removableFrom?: RemovableFrom;
  deletable?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  showArtwork: false,
});

const emit = defineEmits<{
  play: [index: number];
  "play-all": [];
  enqueue: [track: QueueTrack];
  "play-next": [track: QueueTrack];
  "toggle-favorite": [track: QueueTrack];
  share: [track: QueueTrack];
  removed: [trackIds: string[]];
}>();

const { t } = useI18n();
const router = useRouter();
const player = usePlayerStore();
const authStore = useAuthStore();
const toastStore = useToastStore();

const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);
const menuTrack = ref<QueueTrack | null>(null);
const dialogTrack = ref<QueueTrack | null>(null);

const addDialogOpen = ref(false);
const addDialogMode = ref<"library" | "playlist">("library");

const selectedIds = ref<Set<string>>(new Set());
const bulkMode = ref(false);
const confirmOpen = ref(false);
const confirmMode = ref<"single" | "bulk">("single");
const confirmTrack = ref<QueueTrack | null>(null);
const confirmIsDelete = ref(false);
const isRemoving = ref(false);

function openAddDialog(mode: "library" | "playlist") {
  addDialogMode.value = mode;
  addDialogOpen.value = true;
}

function closeAddDialog() {
  addDialogOpen.value = false;
  dialogTrack.value = null;
}

const enrichedTracks = computed<QueueTrack[]>(() => {
  const defaultEnrich: TrackEnrich = { artist_name: props.context ?? "" };
  return props.tracks.map((track) => {
    const fromMap = props.enrich?.get(track.id);
    return toQueueTrack(track, {
      ...defaultEnrich,
      ...fromMap,
    });
  });
});

interface Column {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
}

interface TrackListRow extends Record<string, unknown> {
  id: string;
  index: number;
  num: number;
  title: string;
  artist: string;
  album: string;
  duration: string;
  track: QueueTrack;
  selected: boolean;
}

const canEdit = computed(
  () => props.removableFrom?.canRemove || props.deletable,
);

const columns = computed<Column[]>(() => {
  const cols: Column[] = [];
  if (bulkMode.value && canEdit.value) {
    cols.push({ key: "selected", label: "", align: "center" });
  }
  cols.push(
    { key: "num", label: "#", align: "right" },
    { key: "title", label: t("browse.entities.track"), align: "left" },
    { key: "artist", label: t("browse.entities.artist"), align: "left" },
    { key: "album", label: t("browse.entities.album"), align: "left" },
    { key: "duration", label: t("browse.detail.duration"), align: "right" },
    { key: "actions", label: t("browse.detail.actions"), align: "center" },
  );
  return cols;
});

const rows = computed<Record<string, unknown>[]>(() =>
  enrichedTracks.value.map((track, index) => ({
    id: track.id,
    index,
    num: track.track_number ?? index + 1,
    title: track.title,
    artist: track.artist_name || "—",
    album: track.album_title || "—",
    duration: track.duration != null ? formatTime(track.duration) : "—",
    track,
    selected: selectedIds.value.has(track.id),
  })),
);

const allSelected = computed(() => {
  if (rows.value.length === 0) return false;
  return rows.value.every((row) => asTrackRow(row).selected);
});

const someSelected = computed(() => {
  const selected = rows.value.some((row) => asTrackRow(row).selected);
  return selected && !allSelected.value;
});

function asTrackRow(row: Record<string, unknown>): TrackListRow {
  return row as TrackListRow;
}

function rowKey(row: Record<string, unknown>, index: number): string {
  return row.id ? `${row.id}-${index}` : `row-${index}`;
}

function play(index: number) {
  const track = enrichedTracks.value[index];
  if (!track) return;
  player.playTrack(track, enrichedTracks.value);
  emit("play", index);
}

function playAll() {
  if (enrichedTracks.value.length === 0) return;
  player.playAll(enrichedTracks.value);
  emit("play-all");
}

function openMenu(event: MouseEvent, track: QueueTrack) {
  menuTrack.value = track;
  menuX.value = event.clientX;
  menuY.value = event.clientY;
  menuOpen.value = true;
}

function closeMenu() {
  menuOpen.value = false;
  menuTrack.value = null;
}

function toggleBulkMode() {
  bulkMode.value = !bulkMode.value;
  if (!bulkMode.value) {
    selectedIds.value.clear();
  }
}

function toggleAll() {
  const currentIds = rows.value.map((row) => asTrackRow(row).track.id);
  if (allSelected.value) {
    currentIds.forEach((id) => selectedIds.value.delete(id));
  } else {
    currentIds.forEach((id) => selectedIds.value.add(id));
  }
}

function toggleRow(track: QueueTrack) {
  if (selectedIds.value.has(track.id)) {
    selectedIds.value.delete(track.id);
  } else {
    selectedIds.value.add(track.id);
  }
}

function canDelete(track: QueueTrack): boolean {
  return canManageItem(authStore, track);
}

function openBulkRemove() {
  confirmTrack.value = null;
  confirmMode.value = "bulk";
  confirmIsDelete.value = false;
  confirmOpen.value = true;
}

function openSingleRemove(track: QueueTrack) {
  confirmTrack.value = track;
  confirmMode.value = "single";
  confirmIsDelete.value = false;
  confirmOpen.value = true;
}

function openBulkDelete() {
  confirmTrack.value = null;
  confirmMode.value = "bulk";
  confirmIsDelete.value = true;
  confirmOpen.value = true;
}

function openSingleDelete(track: QueueTrack) {
  confirmTrack.value = track;
  confirmMode.value = "single";
  confirmIsDelete.value = true;
  confirmOpen.value = true;
}

function closeConfirm() {
  confirmOpen.value = false;
  confirmTrack.value = null;
  confirmIsDelete.value = false;
}

const confirmTitle = computed(() => {
  if (confirmIsDelete.value) {
    const entity = t("browse.entities.track");
    if (confirmMode.value === "single" && confirmTrack.value) {
      return t("browse.delete.title", {
        name: confirmTrack.value.title,
        entity,
      });
    }
    return t("browse.delete.bulkTitle", {
      count: selectedIds.value.size,
      entity: t("browse.entities.tracks"),
    });
  }

  if (!props.removableFrom) return "";
  const collection = props.removableFrom.name;
  if (confirmMode.value === "single" && confirmTrack.value) {
    return t("browse.removeFromCollection.singleTitle", {
      track: confirmTrack.value.title,
      collection,
    });
  }
  return t("browse.removeFromCollection.bulkTitle", {
    count: selectedIds.value.size,
    collection,
  });
});

async function onConfirm() {
  const trackIds =
    confirmMode.value === "single" && confirmTrack.value
      ? [confirmTrack.value.id]
      : Array.from(selectedIds.value);

  if (trackIds.length === 0) return;

  isRemoving.value = true;
  try {
    if (confirmIsDelete.value) {
      let deleted = 0;
      const ids: string[] = [];
      for (const id of trackIds) {
        try {
          await deleteTrack(id);
          ids.push(id);
          deleted++;
        } catch (err) {
          if (deleted > 0) break;
          throw err;
        }
      }

      if (confirmMode.value === "single" && confirmTrack.value) {
        toastStore.push({
          type: "success",
          message: t("browse.delete.success", {
            name: confirmTrack.value.title,
            entity: t("browse.entities.track"),
          }),
        });
      } else {
        toastStore.push({
          type: "success",
          message: t("browse.delete.bulkSuccess", {
            count: deleted,
            entity: t("browse.entities.tracks"),
          }),
        });
      }

      emit("removed", ids);
      closeConfirm();
      if (confirmMode.value === "bulk") {
        selectedIds.value.clear();
        bulkMode.value = false;
      }
      return;
    }

    if (!props.removableFrom) return;
    const remove =
      props.removableFrom.type === "library"
        ? removeTracksFromLibrary
        : removeTracksFromPlaylist;
    const response = await remove(props.removableFrom.id, {
      track_ids: trackIds,
    });
    const collection = props.removableFrom.name;

    if (confirmMode.value === "single" && confirmTrack.value) {
      toastStore.push({
        type: "success",
        message: t("browse.removeFromCollection.singleSuccess", {
          track: confirmTrack.value.title,
          collection,
        }),
      });
    } else {
      toastStore.push({
        type: "success",
        message: t("browse.removeFromCollection.bulkSuccess", {
          count: response.removed,
          collection,
        }),
      });
    }

    emit("removed", response.track_ids);
    closeConfirm();
    if (confirmMode.value === "bulk") {
      selectedIds.value.clear();
      bulkMode.value = false;
    }
  } catch (err) {
    const message = confirmIsDelete.value
      ? t("browse.delete.error", {
          entity: t("browse.entities.track"),
          message: getApiErrorMessage(err),
        })
      : t("browse.removeFromCollection.error", {
          message: getApiErrorMessage(err),
        });
    toastStore.push({ type: "error", message });
  } finally {
    isRemoving.value = false;
  }
}

const menuItems = computed(() => {
  const track = menuTrack.value;
  if (!track) return [];

  const isUnfavorite =
    props.favoriteLabel === t("common.unfavorite") ||
    (!props.favoriteLabel && false);

  const items: {
    key: string;
    label: string;
    icon: string;
    danger?: boolean;
  }[] = [
    { key: "play", label: t("common.play"), icon: "play" },
    {
      key: "play-next",
      label: t("browse.contextMenu.playNext"),
      icon: "forward-step",
    },
    {
      key: "enqueue",
      label: t("browse.contextMenu.enqueue"),
      icon: "plus",
    },
  ];

  if (authStore.isAuthenticated) {
    items.push(
      {
        key: "add-to-library",
        label: t("browse.contextMenu.addToLibrary"),
        icon: "folder-plus",
      },
      {
        key: "add-to-playlist",
        label: t("browse.contextMenu.addToPlaylist"),
        icon: "list",
      },
    );
  }

  if (props.removableFrom?.canRemove) {
    const label =
      props.removableFrom.type === "library"
        ? t("browse.contextMenu.removeFromLibrary")
        : t("browse.contextMenu.removeFromPlaylist");
    items.push({
      key: "remove-from-collection",
      label,
      icon: "minus",
      danger: true,
    });
  }

  if (props.deletable && track && canDelete(track)) {
    items.push({
      key: "delete-track",
      label: t("browse.contextMenu.deleteTrack"),
      icon: "trash",
      danger: true,
    });
  }

  items.push({
    key: "favorite",
    label: props.favoriteLabel ?? t("common.favorite"),
    icon: isUnfavorite ? "heart-crack" : "heart",
  });

  if (track.album_id) {
    items.push({
      key: "go-to-album",
      label: t("browse.contextMenu.goToAlbum"),
      icon: "compact-disc",
    });
  }
  if (track.artist_id) {
    items.push({
      key: "go-to-artist",
      label: t("browse.contextMenu.goToArtist"),
      icon: "user",
    });
  }

  items.push({
    key: "share",
    label: t("common.share"),
    icon: "share-nodes",
  });
  return items;
});

function onMenuSelect(key: string) {
  const track = menuTrack.value;
  if (!track) return;
  closeMenu();

  switch (key) {
    case "play": {
      const index = enrichedTracks.value.findIndex((t) => t.id === track.id);
      play(index);
      break;
    }
    case "play-next":
      player.enqueueNext(track);
      emit("play-next", track);
      break;
    case "enqueue":
      player.enqueue(track);
      emit("enqueue", track);
      break;
    case "add-to-library":
      dialogTrack.value = track;
      closeMenu();
      openAddDialog("library");
      break;
    case "add-to-playlist":
      dialogTrack.value = track;
      closeMenu();
      openAddDialog("playlist");
      break;
    case "remove-from-collection":
      openSingleRemove(track);
      break;
    case "delete-track":
      openSingleDelete(track);
      break;
    case "favorite":
      emit("toggle-favorite", track);
      break;
    case "go-to-album":
      if (track.album_id) router.push(`/albums/${track.album_id}`);
      break;
    case "go-to-artist":
      if (track.artist_id) router.push(`/artists/${track.artist_id}`);
      break;
    case "share":
      emit("share", track);
      break;
    default:
      break;
  }
}
</script>

<template>
  <div class="track-list">
    <div class="track-list__header">
      <AppButton
        variant="primary"
        size="sm"
        icon="play"
        :disabled="enrichedTracks.length === 0"
        @click="playAll"
      >
        {{ t("browse.detail.playAll") }}
      </AppButton>

      <div v-if="canEdit" class="track-list__bulk">
        <template v-if="bulkMode">
          <AppButton
            v-if="props.deletable"
            variant="danger"
            size="sm"
            icon="trash"
            :disabled="selectedIds.size === 0 || isRemoving"
            @click="openBulkDelete"
          >
            {{ t("browse.bulkEdit.deleteSelected") }}
          </AppButton>
          <AppButton
            v-if="props.removableFrom?.canRemove"
            variant="danger"
            size="sm"
            icon="minus"
            :disabled="selectedIds.size === 0 || isRemoving"
            @click="openBulkRemove"
          >
            {{ t("browse.bulkEdit.removeSelected") }}
          </AppButton>
          <AppButton
            variant="secondary"
            size="sm"
            icon="xmark"
            :disabled="isRemoving"
            @click="toggleBulkMode"
          >
            {{ t("browse.bulkEdit.done") }}
          </AppButton>
        </template>
        <AppButton
          v-else
          variant="secondary"
          size="sm"
          icon="pen-to-square"
          @click="toggleBulkMode"
        >
          {{ t("browse.bulkEdit.start") }}
        </AppButton>
      </div>
    </div>

    <AppTable
      :columns="columns"
      :rows="rows"
      :row-key="rowKey"
      :loading="props.loading"
      :empty-label="
        props.emptyLabel ??
        t('browse.list.empty', { entity: t('browse.entities.tracks') })
      "
    >
      <template #column-selected>
        <AppCheckbox
          :model-value="allSelected"
          :indeterminate="someSelected"
          :aria-label="t('browse.bulkEdit.selectAll')"
          @update:model-value="toggleAll"
        />
      </template>

      <template #row-selected="{ row }">
        <AppCheckbox
          :model-value="asTrackRow(row).selected"
          :aria-label="t('browse.bulkEdit.selectAll')"
          @update:model-value="toggleRow(asTrackRow(row).track)"
        />
      </template>

      <template #row-title="{ row }">
        <button
          type="button"
          class="track-list__title-btn"
          @click="play(asTrackRow(row).index)"
        >
          <img
            v-if="showArtwork && asTrackRow(row).track.artwork_url"
            :src="asTrackRow(row).track.artwork_url"
            class="track-list__artwork"
            alt=""
          />
          {{ asTrackRow(row).track.title }}
        </button>
      </template>

      <template #row-actions="{ row }">
        <AppButton
          variant="ghost"
          size="sm"
          :aria-label="t('browse.detail.actions')"
          :title="t('browse.detail.actions')"
          icon="ellipsis-vertical"
          :disabled="bulkMode"
          @click="openMenu($event, asTrackRow(row).track)"
        />
      </template>
    </AppTable>

    <ContextMenu
      :open="menuOpen"
      :items="menuItems"
      :x="menuX"
      :y="menuY"
      @select="onMenuSelect"
      @close="closeMenu"
    />

    <AddToCollectionDialog
      v-if="dialogTrack"
      :open="addDialogOpen"
      :mode="addDialogMode"
      item-type="track"
      :item-id="dialogTrack.id"
      :item-name="dialogTrack.title"
      @close="closeAddDialog"
    />

    <AppModal
      v-if="canEdit"
      :open="confirmOpen"
      :title="confirmTitle"
      :closable="!isRemoving"
      @close="closeConfirm"
    >
      <p class="track-list__confirm-text">
        {{ confirmTitle }}
      </p>

      <template #actions>
        <AppButton
          variant="secondary"
          icon="xmark"
          :disabled="isRemoving"
          @click="closeConfirm"
        >
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          variant="danger"
          icon="trash"
          :loading="isRemoving"
          :disabled="isRemoving"
          @click="onConfirm"
        >
          {{
            confirmIsDelete
              ? t("common.delete")
              : t("browse.removeFromCollection.confirm")
          }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.track-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.track-list__header {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

.track-list__bulk {
  display: flex;
  gap: var(--space-2);
}

.track-list__title-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-size: 1rem;
  padding: 0;
  text-align: left;
}

.track-list__title-btn:hover {
  color: var(--color-accent-contrast);
}

.track-list__artwork {
  width: 1.5rem;
  height: 1.5rem;
  border-radius: var(--radius-sm);
  object-fit: cover;
}

.track-list__confirm-text {
  margin: 0;
  color: var(--color-text);
}
</style>
