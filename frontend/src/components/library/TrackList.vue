<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, useRouter } from "vue-router";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import type { TrackResponse, QueueTrack } from "@/player/types";
import { toQueueTrack, type TrackEnrich } from "@/player/enrich";
import AppTable, { type Column } from "@/components/ui/AppTable.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ContextMenu from "@/components/ui/ContextMenu.vue";
import AddToCollectionDialog from "@/components/library/AddToCollectionDialog.vue";
import { formatTime } from "@/utils/time";
import { getApiErrorMessage } from "@/api/client";
import { removeTracksFromLibrary } from "@/api/libraries";
import { removeTracksFromPlaylist } from "@/api/playlists";
import { addFavorite, removeFavorite } from "@/api/favorites";
import {
  deleteTrack,
  deleteTracks,
  downloadTrack,
  enrichTrack,
} from "@/api/tracks";
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
  favoriteManaged?: boolean;
  emptyLabel?: string;
  removableFrom?: RemovableFrom;
  deletable?: boolean;
  autoScroll?: boolean;
  reorderable?: boolean;
  sortBy?: string;
  offset?: number;
  total?: number;
  hasMore?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  showArtwork: false,
  autoScroll: true,
  favoriteManaged: false,
  reorderable: false,
  sortBy: "position",
  offset: 0,
  total: 0,
  hasMore: false,
});

const emit = defineEmits<{
  play: [index: number];
  "play-all": [];
  enqueue: [track: QueueTrack];
  "play-next": [track: QueueTrack];
  "toggle-favorite": [track: QueueTrack];
  share: [track: QueueTrack];
  removed: [trackIds: string[]];
  reorder: [payload: { trackIds: string[]; position?: number }];
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
const reorderMode = ref(false);
const isReordering = ref(false);
const moveToValue = ref("");
const moveToError = ref("");
const listRef = ref<HTMLElement | null>(null);
const favoritedOverrides = ref<Record<string, boolean>>({});

const COMPACT_BREAKPOINT = "(max-width: 1279.98px)";
const isCompact = ref(
  typeof window !== "undefined" &&
    window.matchMedia(COMPACT_BREAKPOINT).matches,
);
let compactMediaQuery: MediaQueryList | null = null;

function updateCompact() {
  isCompact.value = compactMediaQuery ? compactMediaQuery.matches : false;
}

onUnmounted(() => {
  if (compactMediaQuery) {
    compactMediaQuery.removeEventListener("change", updateCompact);
  }
});

function scrollToCurrent() {
  if (!props.autoScroll) return;
  const currentTrack = player.currentTrack;
  if (!currentTrack || !listRef.value) return;

  const currentRow = listRef.value.querySelector(
    ".track-list__row--current, .track-list__compact-item--current",
  ) as HTMLElement | null;
  if (!currentRow || typeof currentRow.scrollIntoView !== "function") return;

  currentRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

onMounted(() => {
  if (typeof window === "undefined") return;
  compactMediaQuery = window.matchMedia(COMPACT_BREAKPOINT);
  updateCompact();
  compactMediaQuery.addEventListener("change", updateCompact);

  if (player.currentTrack) {
    void nextTick(scrollToCurrent);
  }
});

watch(
  () => player.currentTrack,
  () => {
    if (typeof window === "undefined") return;
    void nextTick(scrollToCurrent);
  },
);

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
    const queueTrack = toQueueTrack(track, {
      ...defaultEnrich,
      ...fromMap,
    });
    const override = favoritedOverrides.value[queueTrack.id];
    if (override !== undefined) {
      queueTrack.favorited = override;
    }
    return queueTrack;
  });
});

watch(enrichedTracks, (newTracks, oldTracks) => {
  const currentTrack = player.currentTrack;
  if (!currentTrack || typeof window === "undefined") return;

  const wasPresent = oldTracks.some((t) => t.id === currentTrack.id);
  const isPresent = newTracks.some((t) => t.id === currentTrack.id);

  if (!wasPresent && isPresent) {
    void nextTick(scrollToCurrent);
  }
});

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

const reorderAvailable = computed(
  () => props.reorderable && props.sortBy === "position" && canEdit.value,
);

const columns = computed<Column[]>(() => {
  const cols: Column[] = [];
  if ((bulkMode.value || reorderMode.value) && canEdit.value) {
    cols.push({ key: "selected", label: "", align: "center", width: "2.5rem" });
  }
  if (reorderMode.value && canEdit.value) {
    cols.push({ key: "reorder", label: "", align: "center", width: "3.5rem" });
  }
  cols.push(
    { key: "num", label: "#", align: "right", width: "2rem" },
    { key: "title", label: t("browse.entities.track"), align: "left" },
    { key: "artist", label: t("browse.entities.artist"), align: "left" },
    { key: "album", label: t("browse.entities.album"), align: "left" },
    {
      key: "duration",
      label: t("browse.detail.duration"),
      align: "right",
      width: "4rem",
    },
    {
      key: "actions",
      label: t("browse.detail.actions"),
      align: "center",
      width: "3.5rem",
    },
  );
  return cols;
});

const rows = computed<Record<string, unknown>[]>(() =>
  enrichedTracks.value.map((track, index) => ({
    id: track.id,
    index,
    num:
      props.sortBy === "position"
        ? props.offset + index + 1
        : (track.track_number ?? index + 1),
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

function isTrackFavorited(track: QueueTrack): boolean {
  return favoritedOverrides.value[track.id] ?? track.favorited ?? false;
}

function rowKey(row: Record<string, unknown>, index: number): string {
  return row.id ? `${row.id}-${index}` : `row-${index}`;
}

function isCurrentTrack(track: QueueTrack): boolean {
  return player.currentTrack?.id === track.id;
}

function isPlayingCurrent(track: QueueTrack): boolean {
  return isCurrentTrack(track) && player.isPlaying;
}

function rowClass(row: Record<string, unknown>): string | undefined {
  const classes: string[] = [];
  if (isCurrentTrack(asTrackRow(row).track)) {
    classes.push("track-list__row--current");
  }
  if (
    dragState.value.active &&
    asTrackRow(row).index === dragState.value.dropTargetIndex
  ) {
    classes.push("track-list__row--drop-target");
  }
  return classes.length > 0 ? classes.join(" ") : undefined;
}

function play(index: number) {
  const track = enrichedTracks.value[index];
  if (!track) return;
  const wasCurrent = isCurrentTrack(track);
  player.playTrack(track, enrichedTracks.value);
  if (wasCurrent) {
    void nextTick(scrollToCurrent);
  }
  emit("play", index);
}

function playAll() {
  if (enrichedTracks.value.length === 0) return;
  player.playAll(enrichedTracks.value);
  emit("play-all");
}

function openMenu(event: MouseEvent, track: QueueTrack) {
  menuTrack.value = track;
  const trigger = event.currentTarget as HTMLElement | null;
  if (trigger) {
    const rect = trigger.getBoundingClientRect();
    menuX.value = Math.round(rect.right);
    menuY.value = Math.round(rect.bottom);
  } else {
    menuX.value = event.clientX;
    menuY.value = event.clientY;
  }
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

function toggleReorderMode() {
  reorderMode.value = !reorderMode.value;
  if (!reorderMode.value) {
    selectedIds.value.clear();
    moveToValue.value = "";
    moveToError.value = "";
  }
}

function selectedTrackIds(): string[] {
  return enrichedTracks.value
    .filter((track) => selectedIds.value.has(track.id))
    .map((track) => track.id);
}

function firstSelectedIndex(): number {
  return enrichedTracks.value.findIndex((track) =>
    selectedIds.value.has(track.id),
  );
}

function lastSelectedIndex(): number {
  for (let i = enrichedTracks.value.length - 1; i >= 0; i--) {
    if (selectedIds.value.has(enrichedTracks.value[i]!.id)) {
      return i;
    }
  }
  return -1;
}

function canMoveUp(): boolean {
  if (!reorderMode.value || isReordering.value) return false;
  const first = firstSelectedIndex();
  if (selectedIds.value.size === 0) return false;
  return first > 0 || props.offset > 0;
}

function canMoveDown(): boolean {
  if (!reorderMode.value || isReordering.value) return false;
  const last = lastSelectedIndex();
  if (selectedIds.value.size === 0) return false;
  return last < enrichedTracks.value.length - 1 || props.hasMore;
}

function canMoveRowUp(rowIndex: number): boolean {
  if (!reorderMode.value || isReordering.value) return false;
  return rowIndex > 0 || props.offset > 0;
}

function canMoveRowDown(rowIndex: number): boolean {
  if (!reorderMode.value || isReordering.value) return false;
  return rowIndex < enrichedTracks.value.length - 1 || props.hasMore;
}

function emitReorder(trackIds: string[], position?: number) {
  if (trackIds.length === 0) return;
  isReordering.value = true;
  emit("reorder", { trackIds, position });
}

function moveUp() {
  const trackIds = selectedTrackIds();
  if (trackIds.length === 0) return;

  const first = firstSelectedIndex();
  if (first === 0 && props.offset > 0) {
    emitReorder(trackIds, 1);
    return;
  }
  if (first > 0) {
    emitReorder(trackIds, props.offset + first);
  }
}

function moveDown() {
  const trackIds = selectedTrackIds();
  if (trackIds.length === 0) return;

  const first = firstSelectedIndex();
  const last = lastSelectedIndex();
  if (last === enrichedTracks.value.length - 1 && props.hasMore) {
    emitReorder(trackIds, undefined);
    return;
  }
  if (last < enrichedTracks.value.length - 1) {
    emitReorder(trackIds, props.offset + first + 2);
  }
}

function moveRowUp(rowIndex: number) {
  if (rowIndex === 0 && props.offset > 0) {
    emitReorder([enrichedTracks.value[rowIndex]!.id], 1);
    return;
  }
  if (rowIndex > 0) {
    emitReorder([enrichedTracks.value[rowIndex]!.id], props.offset + rowIndex);
  }
}

function moveRowDown(rowIndex: number) {
  if (rowIndex === enrichedTracks.value.length - 1 && props.hasMore) {
    emitReorder([enrichedTracks.value[rowIndex]!.id], undefined);
    return;
  }
  if (rowIndex < enrichedTracks.value.length - 1) {
    emitReorder(
      [enrichedTracks.value[rowIndex]!.id],
      props.offset + rowIndex + 2,
    );
  }
}

function moveToPosition() {
  const value = moveToValue.value.trim();
  if (value === "") return;

  const n = Number(value);
  if (Number.isNaN(n)) {
    moveToError.value = t("browse.reorder.invalidPosition");
    return;
  }

  if (n === 0) {
    moveToError.value = t("browse.reorder.invalidPosition");
    return;
  }

  if (n === -1) {
    emitMoveTo(undefined);
    return;
  }

  if (n < 0) {
    if (props.total > 0) {
      emitMoveTo(props.total + n + 1);
    } else {
      emitMoveTo(undefined);
    }
    return;
  }

  emitMoveTo(n);
}

function emitMoveTo(position?: number) {
  const trackIds = selectedTrackIds();
  if (trackIds.length === 0) return;
  emitReorder(trackIds, position);
}

function moveToTop() {
  const trackIds = selectedTrackIds();
  if (trackIds.length === 0) return;
  emitReorder(trackIds, 1);
}

function moveToBottom() {
  const trackIds = selectedTrackIds();
  if (trackIds.length === 0) return;
  emitReorder(trackIds, undefined);
}

watch(
  () => props.tracks,
  () => {
    isReordering.value = false;
    moveToValue.value = "";
    moveToError.value = "";
  },
);

function setReordering(value: boolean) {
  isReordering.value = value;
}

defineExpose({ setReordering });

const dragState = ref<{
  active: boolean;
  pointerDown: boolean;
  draggedRow: QueueTrack | null;
  startY: number;
  startX: number;
  longPressTimer: ReturnType<typeof setTimeout> | null;
  dropTargetIndex: number;
}>({
  active: false,
  pointerDown: false,
  draggedRow: null,
  startY: 0,
  startX: 0,
  longPressTimer: null,
  dropTargetIndex: -1,
});

const DRAG_THRESHOLD = 8;
const LONG_PRESS_MS = 300;

function resetDrag() {
  if (dragState.value.longPressTimer) {
    clearTimeout(dragState.value.longPressTimer);
  }
  dragState.value = {
    active: false,
    pointerDown: false,
    draggedRow: null,
    startY: 0,
    startX: 0,
    longPressTimer: null,
    dropTargetIndex: -1,
  };
}

function onHandlePointerDown(event: PointerEvent, track: QueueTrack) {
  if (!reorderMode.value || isReordering.value) return;
  if (typeof window === "undefined") return;

  const target = event.currentTarget as HTMLElement | null;
  if (target && "setPointerCapture" in target) {
    target.setPointerCapture(event.pointerId);
  }

  dragState.value = {
    active: false,
    pointerDown: true,
    draggedRow: track,
    startY: event.clientY,
    startX: event.clientX,
    longPressTimer: null,
    dropTargetIndex: -1,
  };

  const isTouch = event.pointerType === "touch";
  if (isTouch) {
    dragState.value.longPressTimer = setTimeout(() => {
      if (dragState.value.pointerDown && dragState.value.draggedRow) {
        dragState.value.active = true;
      }
    }, LONG_PRESS_MS);
  }
}

function onHandlePointerMove(event: PointerEvent) {
  if (!dragState.value.pointerDown) return;

  const dx = Math.abs(event.clientX - dragState.value.startX);
  const dy = Math.abs(event.clientY - dragState.value.startY);

  if (!dragState.value.active && (dx > DRAG_THRESHOLD || dy > DRAG_THRESHOLD)) {
    if (dragState.value.longPressTimer) {
      clearTimeout(dragState.value.longPressTimer);
      dragState.value.longPressTimer = null;
    }
    dragState.value.active = true;
  }

  if (!dragState.value.active) return;

  const list = listRef.value;
  if (!list) return;

  const pointerY = event.clientY;
  const rowElements = Array.from(
    list.querySelectorAll(".app-table tbody tr, .track-list__compact-item"),
  ) as HTMLElement[];

  let targetIndex = rowElements.length;
  for (let i = 0; i < rowElements.length; i++) {
    const rect = rowElements[i]!.getBoundingClientRect();
    if (pointerY < rect.top + rect.height / 2) {
      targetIndex = i;
      break;
    }
  }

  dragState.value.dropTargetIndex = targetIndex;
}

function onHandlePointerUp() {
  if (!dragState.value.pointerDown) return;

  if (dragState.value.longPressTimer) {
    clearTimeout(dragState.value.longPressTimer);
    dragState.value.longPressTimer = null;
  }

  if (
    dragState.value.active &&
    dragState.value.draggedRow &&
    dragState.value.dropTargetIndex !== -1
  ) {
    const track = dragState.value.draggedRow;
    const index = dragState.value.dropTargetIndex;

    let trackIds: string[];
    if (selectedIds.value.has(track.id) && selectedIds.value.size > 0) {
      trackIds = selectedTrackIds();
    } else {
      trackIds = [track.id];
    }

    const position =
      index < enrichedTracks.value.length
        ? props.offset + index + 1
        : props.offset + index + 1;

    if (trackIds.length > 0) {
      emitReorder(trackIds, position);
    }
  }

  resetDrag();
}

function onHandlePointerCancel() {
  resetDrag();
}

function onKeyDown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    resetDrag();
  }
}

onMounted(() => {
  if (typeof window === "undefined") return;
  window.addEventListener("keydown", onKeyDown);
});

onUnmounted(() => {
  if (typeof window === "undefined") return;
  window.removeEventListener("keydown", onKeyDown);
  resetDrag();
});

function canManageTrack(track: QueueTrack): boolean {
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
      let ids: string[] = [];
      if (confirmMode.value === "single" && confirmTrack.value) {
        await deleteTrack(confirmTrack.value.id);
        ids = [confirmTrack.value.id];
        deleted = 1;
      } else {
        const response = await deleteTracks(trackIds);
        ids = response.track_ids;
        deleted = response.deleted;
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

const isFavoriteManaged = computed(() => props.favoriteManaged);

const menuItems = computed(() => {
  const track = menuTrack.value;
  if (!track) return [];

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

  if (track.audio_url) {
    items.push({
      key: "download",
      label: t("common.download"),
      icon: "download",
    });
  }

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

  if (track && canManageTrack(track)) {
    items.push({
      key: "edit-track",
      label: t("browse.contextMenu.editTrack"),
      icon: "pen-to-square",
    });
  }

  if (track && canManageTrack(track)) {
    items.push({
      key: "enrich",
      label: t("browse.contextMenu.enrich"),
      icon: "wand-magic-sparkles",
    });
  }

  if (props.deletable && track && canManageTrack(track)) {
    items.push({
      key: "delete-track",
      label: t("browse.contextMenu.deleteTrack"),
      icon: "trash",
      danger: true,
    });
  }

  if (authStore.isAuthenticated) {
    const favorited = isTrackFavorited(track);
    items.push({
      key: "favorite",
      label:
        props.favoriteLabel ??
        (favorited ? t("common.unfavorite") : t("common.favorite")),
      icon: favorited ? "heart-crack" : "heart",
    });
  }

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

async function onMenuSelect(key: string) {
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
    case "download":
      try {
        await downloadTrack(track.audio_url!, track.title);
      } catch (err) {
        toastStore.push({
          type: "error",
          message: t("browse.download.error", {
            message: getApiErrorMessage(err) || t("errors.unknown"),
          }),
        });
      }
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
    case "edit-track":
      await router.push({
        name: "trackEdit",
        params: { id: track.id },
      });
      break;
    case "delete-track":
      openSingleDelete(track);
      break;
    case "favorite": {
      if (isFavoriteManaged.value) {
        emit("toggle-favorite", track);
        break;
      }

      const currentlyFavorited = isTrackFavorited(track);
      try {
        if (currentlyFavorited) {
          await removeFavorite(track.id);
          toastStore.push({
            type: "success",
            message: t("common.favoriteRemoved"),
          });
        } else {
          await addFavorite(track.id);
          toastStore.push({
            type: "success",
            message: t("common.favoriteAdded"),
          });
        }
        favoritedOverrides.value[track.id] = !currentlyFavorited;
        track.favorited = !currentlyFavorited;
        emit("toggle-favorite", track);
      } catch (err) {
        const message = getApiErrorMessage(err) || t("errors.unknown");
        const key = currentlyFavorited
          ? "common.favoriteRemoveError"
          : "common.favoriteAddError";
        toastStore.push({ type: "error", message: t(key, { message }) });
      }
      break;
    }
    case "go-to-album":
      if (track.album_id) router.push(`/albums/${track.album_id}`);
      break;
    case "go-to-artist":
      if (track.artist_id) router.push(`/artists/${track.artist_id}`);
      break;
    case "enrich":
      try {
        await enrichTrack(track.id);
        toastStore.push({
          type: "success",
          message: t("browse.enrich.success"),
        });
      } catch (err) {
        toastStore.push({
          type: "error",
          message: t("browse.enrich.error", {
            message: getApiErrorMessage(err),
          }),
        });
      }
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
  <div ref="listRef" class="track-list">
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
        <template v-if="reorderMode">
          <AppButton
            variant="secondary"
            size="sm"
            :disabled="isReordering"
            @click="toggleReorderMode"
          >
            {{ t("browse.reorder.done") }}
          </AppButton>
          <AppButton
            variant="ghost"
            size="sm"
            icon="chevron-up"
            :disabled="!canMoveUp()"
            @click="moveUp"
          >
            {{ t("browse.reorder.moveUp") }}
          </AppButton>
          <AppButton
            variant="ghost"
            size="sm"
            icon="chevron-down"
            :disabled="!canMoveDown()"
            @click="moveDown"
          >
            {{ t("browse.reorder.moveDown") }}
          </AppButton>
          <span class="track-list__move-to">
            <input
              v-model="moveToValue"
              type="text"
              inputmode="numeric"
              class="track-list__move-to-input"
              :placeholder="t('browse.reorder.moveToPosition')"
              :disabled="isReordering"
              @keyup.enter="moveToPosition"
            />
            <AppButton
              variant="ghost"
              size="sm"
              :disabled="isReordering || selectedIds.size === 0"
              @click="moveToTop"
            >
              {{ t("browse.reorder.top") }}
            </AppButton>
            <AppButton
              variant="ghost"
              size="sm"
              :disabled="isReordering || selectedIds.size === 0"
              @click="moveToBottom"
            >
              {{ t("browse.reorder.bottom") }}
            </AppButton>
          </span>
          <span v-if="moveToError" class="track-list__move-to-error">{{
            moveToError
          }}</span>
        </template>
        <template v-else-if="reorderAvailable">
          <AppButton
            variant="secondary"
            size="sm"
            icon="arrow-up-arrow-down"
            :disabled="isReordering"
            @click="toggleReorderMode"
          >
            {{ t("browse.reorder.start") }}
          </AppButton>
        </template>
        <template v-if="bulkMode && !reorderMode">
          <AppCheckbox
            v-if="isCompact"
            :model-value="allSelected"
            :indeterminate="someSelected"
            :label="t('browse.bulkEdit.selectAll')"
            @update:model-value="toggleAll"
          />
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
      v-if="!isCompact"
      :columns="columns"
      :rows="rows"
      :row-key="rowKey"
      :row-class="rowClass"
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

      <template #row-reorder="{ row }">
        <div
          class="track-list__reorder-cell"
          :class="{ 'track-list__reorder-cell--dragging': dragState.active }"
        >
          <button
            type="button"
            class="track-list__handle"
            :class="{ 'track-list__handle--grabbing': dragState.active }"
            :aria-label="t('browse.reorder.drag')"
            :disabled="isReordering"
            @pointerdown="onHandlePointerDown($event, asTrackRow(row).track)"
            @pointermove="onHandlePointerMove"
            @pointerup="onHandlePointerUp"
            @pointercancel="onHandlePointerCancel"
          >
            <AppIcon name="grip-vertical" />
          </button>
          <div class="track-list__reorder-buttons">
            <AppButton
              variant="ghost"
              size="sm"
              icon="chevron-up"
              :aria-label="t('browse.reorder.moveUp')"
              :disabled="!canMoveRowUp(asTrackRow(row).index)"
              @click="moveRowUp(asTrackRow(row).index)"
            />
            <AppButton
              variant="ghost"
              size="sm"
              icon="chevron-down"
              :aria-label="t('browse.reorder.moveDown')"
              :disabled="!canMoveRowDown(asTrackRow(row).index)"
              @click="moveRowDown(asTrackRow(row).index)"
            />
          </div>
        </div>
      </template>

      <template #row-num="{ row }">
        <span
          v-if="isCurrentTrack(asTrackRow(row).track)"
          class="track-list__playing"
          :class="{
            'track-list__playing--active': isPlayingCurrent(
              asTrackRow(row).track,
            ),
          }"
          :aria-label="t('player.nowPlaying')"
        >
          <span
            v-for="bar in 3"
            :key="bar"
            class="track-list__playing-bar"
            aria-hidden="true"
          />
        </span>
        <span v-else class="track-list__num">
          {{ asTrackRow(row).num }}
        </span>
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
          <span
            :title="asTrackRow(row).track.title"
            class="track-list__title-text"
          >
            {{ asTrackRow(row).track.title }}
          </span>
          <AppIcon
            v-if="isTrackFavorited(asTrackRow(row).track)"
            name="heart"
            variant="solid"
            class="track-list__favorite-icon"
            :aria-label="t('common.favorite')"
          />
        </button>
      </template>

      <template #row-artist="{ row }">
        <RouterLink
          v-if="asTrackRow(row).track.artist_id"
          :to="`/artists/${asTrackRow(row).track.artist_id}`"
          :title="asTrackRow(row).artist"
          class="track-list__link"
        >
          {{ asTrackRow(row).artist }}
        </RouterLink>
        <span
          v-else
          :title="asTrackRow(row).artist"
          class="track-list__cell-text"
        >
          {{ asTrackRow(row).artist }}
        </span>
      </template>

      <template #row-album="{ row }">
        <RouterLink
          v-if="asTrackRow(row).track.album_id"
          :to="`/albums/${asTrackRow(row).track.album_id}`"
          :title="asTrackRow(row).album"
          class="track-list__link"
        >
          {{ asTrackRow(row).album }}
        </RouterLink>
        <span
          v-else
          :title="asTrackRow(row).album"
          class="track-list__cell-text"
        >
          {{ asTrackRow(row).album }}
        </span>
      </template>

      <template #row-actions="{ row }">
        <AppButton
          variant="ghost"
          size="sm"
          :aria-label="t('browse.detail.actions')"
          :title="t('browse.detail.actions')"
          icon="ellipsis-vertical"
          :disabled="bulkMode || reorderMode"
          @click="openMenu($event, asTrackRow(row).track)"
        />
      </template>
    </AppTable>
    <ul
      v-else
      class="track-list__compact"
      role="list"
      :aria-busy="props.loading ? 'true' : 'false'"
    >
      <template v-if="props.loading">
        <li
          v-for="i in 3"
          :key="`loading-${i}`"
          class="track-list__compact-item track-list__compact-item--loading"
        >
          <SkeletonLoader variant="list-row" />
        </li>
      </template>
      <li v-else-if="rows.length === 0" class="track-list__compact-empty">
        {{
          props.emptyLabel ??
          t("browse.list.empty", { entity: t("browse.entities.tracks") })
        }}
      </li>
      <template v-else>
        <li
          v-for="(row, index) in rows"
          :key="rowKey(row, index)"
          class="track-list__compact-item"
          :class="{
            'track-list__compact-item--current': isCurrentTrack(
              asTrackRow(row).track,
            ),
            'track-list__compact-item--drop-target':
              dragState.active &&
              asTrackRow(row).index === dragState.dropTargetIndex,
          }"
        >
          <div
            v-if="(bulkMode || reorderMode) && canEdit"
            class="track-list__compact-select"
          >
            <AppCheckbox
              :model-value="asTrackRow(row).selected"
              :aria-label="t('browse.bulkEdit.selectAll')"
              @update:model-value="toggleRow(asTrackRow(row).track)"
            />
          </div>
          <template v-if="reorderMode && canEdit">
            <div
              class="track-list__compact-reorder"
              :class="{
                'track-list__compact-reorder--dragging': dragState.active,
              }"
            >
              <button
                type="button"
                class="track-list__compact-handle"
                :class="{
                  'track-list__compact-handle--grabbing': dragState.active,
                }"
                :aria-label="t('browse.reorder.drag')"
                :disabled="isReordering"
                @pointerdown="
                  onHandlePointerDown($event, asTrackRow(row).track)
                "
                @pointermove="onHandlePointerMove"
                @pointerup="onHandlePointerUp"
                @pointercancel="onHandlePointerCancel"
              >
                <AppIcon name="grip-vertical" />
              </button>
              <div class="track-list__compact-reorder-buttons">
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon="chevron-up"
                  :aria-label="t('browse.reorder.moveUp')"
                  :disabled="!canMoveRowUp(index)"
                  @click="moveRowUp(index)"
                />
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon="chevron-down"
                  :aria-label="t('browse.reorder.moveDown')"
                  :disabled="!canMoveRowDown(index)"
                  @click="moveRowDown(index)"
                />
              </div>
            </div>
          </template>
          <span
            v-if="isCurrentTrack(asTrackRow(row).track)"
            class="track-list__compact-number track-list__playing"
            :class="{
              'track-list__playing--active': isPlayingCurrent(
                asTrackRow(row).track,
              ),
            }"
            :aria-label="t('player.nowPlaying')"
          >
            <span
              v-for="bar in 3"
              :key="bar"
              class="track-list__playing-bar"
              aria-hidden="true"
            />
          </span>
          <span v-else class="track-list__compact-number" aria-hidden="true">
            {{ asTrackRow(row).num }}
          </span>

          <div class="track-list__compact-main">
            <button
              type="button"
              class="track-list__compact-title"
              :title="asTrackRow(row).track.title"
              @click="play(asTrackRow(row).index)"
            >
              {{ asTrackRow(row).track.title }}
              <AppIcon
                v-if="isTrackFavorited(asTrackRow(row).track)"
                name="heart"
                variant="solid"
                class="track-list__favorite-icon"
                :aria-label="t('common.favorite')"
              />
            </button>
            <RouterLink
              v-if="asTrackRow(row).track.artist_id"
              :to="`/artists/${asTrackRow(row).track.artist_id}`"
              :title="asTrackRow(row).artist"
              class="track-list__compact-artist"
            >
              {{ asTrackRow(row).artist }}
            </RouterLink>
            <span
              v-else
              class="track-list__compact-artist"
              :title="asTrackRow(row).artist"
            >
              {{ asTrackRow(row).artist }}
            </span>
          </div>

          <span class="track-list__compact-duration">
            {{ asTrackRow(row).duration }}
          </span>

          <AppButton
            variant="ghost"
            size="sm"
            :aria-label="t('browse.detail.actions')"
            :title="t('browse.detail.actions')"
            icon="ellipsis-vertical"
            :disabled="bulkMode || reorderMode"
            @click="openMenu($event, asTrackRow(row).track)"
          />
        </li>
      </template>
    </ul>

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
  flex-wrap: wrap;
  gap: var(--space-3);
}

.track-list__bulk {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.track-list__title-btn {
  display: inline-flex;
  align-items: center;
  width: 100%;
  min-width: 0;
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

.track-list__title-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-list__cell-text {
  display: block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-list__link {
  color: var(--color-text-secondary);
  text-decoration: none;
  display: block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-list__link:hover {
  text-decoration: underline;
}

.track-list__artwork {
  width: 1.5rem;
  height: 1.5rem;
  border-radius: var(--radius-sm);
  object-fit: cover;
  flex-shrink: 0;
}

.track-list__favorite-icon {
  color: var(--color-danger);
  font-size: 0.75rem;
  flex-shrink: 0;
}

.track-list__confirm-text {
  margin: 0;
  color: var(--color-text);
}

.track-list :deep(.app-table) {
  table-layout: fixed;
}

.track-list :deep(.app-table th),
.track-list :deep(.app-table td) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-list__compact {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  color: var(--color-text);
}

.track-list__compact-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  min-width: 0;
  transition: background-color 0.2s;
}

.track-list__compact-item:hover {
  background-color: var(--color-surface-hover);
}

.track-list__compact-item--loading {
  padding: var(--space-2) 0;
}

.track-list__compact-empty {
  padding: var(--space-6) var(--space-3);
  text-align: center;
  color: var(--color-text-muted);
}

.track-list__compact-number,
.track-list__compact-select {
  width: 1.25rem;
  flex-shrink: 0;
  text-align: center;
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}

.track-list__compact-select {
  display: flex;
  align-items: center;
  justify-content: center;
}

.track-list__compact-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
}

.track-list__compact-title {
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  padding: 0;
  text-align: left;
  font: inherit;
}

.track-list__compact-title:hover {
  color: var(--color-accent-contrast);
}

.track-list__compact-artist {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
  text-decoration: none;
}

a.track-list__compact-artist {
  color: var(--color-text-muted);
  font-weight: 600;
}

a.track-list__compact-artist:hover {
  text-decoration: underline;
}

.track-list__compact-duration {
  flex-shrink: 0;
  min-width: 2.5rem;
  text-align: right;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.track-list :deep(.app-table tr.track-list__row--current) {
  background-color: var(--color-surface-raised);
}

.track-list :deep(.app-table tr.track-list__row--current:hover) {
  background-color: var(--color-surface-raised);
}

.track-list__compact-item--current {
  background-color: var(--color-surface-raised);
}

.track-list__compact-item--current:hover {
  background-color: var(--color-surface-raised);
}

.track-list
  :deep(.app-table tr.track-list__row--current)
  .app-table__cell--artist,
.track-list
  :deep(.app-table tr.track-list__row--current)
  .app-table__cell--album,
.track-list
  :deep(.app-table tr.track-list__row--current)
  .app-table__cell--duration,
.track-list__compact-item--current .track-list__compact-artist,
.track-list__compact-item--current .track-list__compact-duration,
.track-list__compact-item--current .track-list__compact-number {
  color: var(--color-text);
}

.track-list__playing {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  width: 0.75rem;
  height: 0.75rem;
  color: currentColor;
}

.track-list__playing-bar {
  flex: 1;
  width: 2px;
  min-height: 2px;
  background-color: currentColor;
  border-radius: 1px;
  height: 30%;
}

.track-list__playing--active .track-list__playing-bar {
  height: 20%;
  animation: track-list-playing 0.6s ease-in-out infinite alternate;
}

.track-list__playing--active .track-list__playing-bar:nth-child(2) {
  animation-delay: 0.15s;
}

.track-list__playing--active .track-list__playing-bar:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes track-list-playing {
  0% {
    height: 20%;
  }
  100% {
    height: 100%;
  }
}

.track-list__reorder-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
}

.track-list__reorder-cell--dragging {
  opacity: 0.6;
}

.track-list__handle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1);
  background: transparent;
  border: none;
  color: var(--color-text-muted);
  cursor: grab;
  font-size: 1rem;
  touch-action: none;
}

.track-list__handle:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.track-list__handle--grabbing,
.track-list__handle:active {
  cursor: grabbing;
}

.track-list__reorder-buttons {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.track-list__move-to {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.track-list__move-to-input {
  width: 5rem;
  padding: var(--space-1) var(--space-2);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  font: inherit;
}

.track-list__move-to-input:disabled {
  opacity: 0.5;
}

.track-list__move-to-error {
  color: var(--color-danger);
  font-size: 0.875rem;
}

.track-list__compact-reorder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  width: 2.5rem;
}

.track-list__compact-reorder--dragging {
  opacity: 0.6;
}

.track-list__compact-handle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  background: transparent;
  border: none;
  color: var(--color-text-muted);
  cursor: grab;
  font-size: 1rem;
  touch-action: none;
}

.track-list__compact-handle:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.track-list__compact-handle--grabbing,
.track-list__compact-handle:active {
  cursor: grabbing;
}

.track-list__compact-reorder-buttons {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.track-list__drop-indicator {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  background-color: var(--color-accent);
  pointer-events: none;
  z-index: 1;
}

.track-list :deep(.app-table tr.track-list__row--drop-target) td {
  border-top: 2px solid var(--color-accent);
}

.track-list__compact-item--drop-target {
  border-top: 2px solid var(--color-accent);
}

@media (prefers-reduced-motion: reduce) {
  .track-list__playing-bar {
    animation: none !important;
  }
}
</style>
