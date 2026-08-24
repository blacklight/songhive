<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { usePlayerStore } from "@/stores/player";
import type { TrackResponse, QueueTrack } from "@/player/types";
import { toQueueTrack, type TrackEnrich } from "@/player/enrich";
import AppTable from "@/components/ui/AppTable.vue";
import AppButton from "@/components/ui/AppButton.vue";
import ContextMenu from "@/components/ui/ContextMenu.vue";
import { formatTime } from "@/utils/time";

export interface Props {
  tracks: TrackResponse[];
  context?: string;
  loading?: boolean;
  showArtwork?: boolean;
  enrich?: Map<string, TrackEnrich>;
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
}>();

const { t } = useI18n();
const router = useRouter();
const player = usePlayerStore();

const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);
const menuTrack = ref<QueueTrack | null>(null);

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
}

const columns: Column[] = [
  { key: "num", label: "#", align: "right" },
  { key: "title", label: t("browse.entities.track"), align: "left" },
  { key: "artist", label: t("browse.entities.artist"), align: "left" },
  { key: "album", label: t("browse.entities.album"), align: "left" },
  { key: "duration", label: t("browse.detail.duration"), align: "right" },
  { key: "actions", label: t("browse.detail.actions"), align: "center" },
];

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
  })),
);

function asTrackRow(row: Record<string, unknown>): TrackListRow {
  return row as TrackListRow;
}

function rowKey(row: Record<string, unknown>, index: number): string {
  return String(row.id ?? `row-${index}`);
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

const menuItems = computed(() => {
  const track = menuTrack.value;
  if (!track) return [];

  const items = [
    { key: "play", label: t("common.play") },
    { key: "play-next", label: t("browse.contextMenu.playNext") },
    { key: "enqueue", label: t("browse.contextMenu.enqueue") },
    { key: "favorite", label: t("common.favorite") },
  ];

  if (track.album_id) {
    items.push({
      key: "go-to-album",
      label: t("browse.contextMenu.goToAlbum"),
    });
  }
  if (track.artist_id) {
    items.push({
      key: "go-to-artist",
      label: t("browse.contextMenu.goToArtist"),
    });
  }

  items.push({ key: "share", label: t("common.share") });
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
        :disabled="enrichedTracks.length === 0"
        @click="playAll"
      >
        {{ t("browse.detail.playAll") }}
      </AppButton>
    </div>
    <AppTable
      :columns="columns"
      :rows="rows"
      :row-key="rowKey"
      :loading="props.loading"
      :empty-label="
        t('browse.list.empty', { entity: t('browse.entities.tracks') })
      "
    >
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
          @click="openMenu($event, asTrackRow(row).track)"
        >
          ⋮
        </AppButton>
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
</style>
