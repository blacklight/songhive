<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { getTrack, listTracks, type TrackResponse } from "@/api/tracks";
import { getAlbum } from "@/api/albums";
import { getArtist } from "@/api/artists";
import { getPlaylist, listPlaylistTracks } from "@/api/playlists";
import { getLibrary, listLibraryTracks } from "@/api/libraries";
import type { ShareItemType } from "@/api/shares";
import { useInstanceStore } from "@/stores/instance";
import { getPublicUrl, toAbsoluteUrl } from "@/utils/share";
import { EMBED_LIST_MAX_TRACKS } from "@/utils/embed";
import { formatTime } from "@/utils/time";
import AppIcon from "@/components/ui/AppIcon.vue";

const { t } = useI18n();
const route = useRoute();
const instanceStore = useInstanceStore();

const entityType = computed(() => String(route.params.type) as ShareItemType);
const entityId = computed(() => String(route.params.id));

const loading = ref(true);
const failed = ref(false);

const track = ref<TrackResponse | null>(null);
const tracks = ref<TrackResponse[]>([]);
const title = ref("");
const subtitle = ref("");
const coverUrl = ref<string | null>(null);

const expandedId = ref<string | null>(null);

const pageUrl = computed(() => getPublicUrl(entityType.value, entityId.value));

const audioSrc = computed(() =>
  track.value?.audio_url ? toAbsoluteUrl(track.value.audio_url) : null,
);

function trackAudioSrc(item: TrackResponse): string | null {
  return item.audio_url ? toAbsoluteUrl(item.audio_url) : null;
}

function toggle(id: string) {
  expandedId.value = expandedId.value === id ? null : id;
}

const PAGE_SIZE = 100;

async function fetchCollectionTracks(): Promise<TrackResponse[]> {
  const id = entityId.value;
  const collected: TrackResponse[] = [];
  while (collected.length < EMBED_LIST_MAX_TRACKS) {
    const query = {
      limit: PAGE_SIZE,
      offset: collected.length,
      include: "artist,album",
    };
    let page: TrackResponse[];
    switch (entityType.value) {
      case "album":
        page = await listTracks({ album_id: id, ...query });
        break;
      case "artist":
        page = await listTracks({ artist_id: id, ...query });
        break;
      case "playlist":
        page = await listPlaylistTracks(id, query);
        break;
      case "library":
        page = await listLibraryTracks(id, query);
        break;
      default:
        page = [];
    }
    collected.push(...page);
    if (page.length < PAGE_SIZE) break;
  }
  return collected;
}

async function loadCollection() {
  const id = entityId.value;
  const [entity, items] = await Promise.all([
    (async () => {
      switch (entityType.value) {
        case "album": {
          const album = await getAlbum(id, { include: "artist" });
          return {
            title: album.title,
            subtitle: album.artist?.name ?? "",
            cover: album.cover_url ?? null,
          };
        }
        case "artist": {
          const artist = await getArtist(id);
          return {
            title: artist.name,
            subtitle: "",
            cover: artist.image_url ?? artist.cover_url ?? null,
          };
        }
        case "playlist": {
          const playlist = await getPlaylist(id);
          return {
            title: playlist.name,
            subtitle: "",
            cover: playlist.image_url ?? playlist.cover_url ?? null,
          };
        }
        case "library": {
          const library = await getLibrary(id);
          return {
            title: library.name,
            subtitle: "",
            cover: library.image_url ?? library.cover_url ?? null,
          };
        }
        default:
          return { title: "", subtitle: "", cover: null };
      }
    })(),
    fetchCollectionTracks(),
  ]);

  title.value = entity.title;
  subtitle.value = entity.subtitle;
  coverUrl.value = entity.cover;

  const visible = items.filter(
    (item) => item.visibility === "public" && item.audio_url,
  );
  if (entityType.value === "album") {
    visible.sort(
      (a, b) =>
        (a.disc_number ?? 0) - (b.disc_number ?? 0) ||
        (a.track_number ?? 0) - (b.track_number ?? 0),
    );
  }
  tracks.value = visible;
}

async function load() {
  loading.value = true;
  failed.value = false;
  expandedId.value = null;
  try {
    if (entityType.value === "track") {
      const result = await getTrack(entityId.value, {
        include: "artist,album",
      });
      track.value = result;
      title.value = result.title || result.filename || "";
      subtitle.value = [result.artist?.name, result.album?.title]
        .filter(Boolean)
        .join(" · ");
      coverUrl.value = result.image_url ?? result.album?.cover_url ?? null;
    } else {
      track.value = null;
      await loadCollection();
    }
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

// The iframe auto-resize protocol: embed.js listens for these messages and
// adjusts the iframe height to the rendered content.
function reportHeight() {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      {
        source: "songhive-embed",
        type: "resize",
        height: Math.ceil(document.documentElement.scrollHeight),
      },
      "*",
    );
  }
}

let resizeObserver: ResizeObserver | null = null;

onMounted(() => {
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(reportHeight);
    resizeObserver.observe(document.documentElement);
  }
  reportHeight();
});

onUnmounted(() => {
  resizeObserver?.disconnect();
});

watch(
  () => [entityType.value, entityId.value],
  () => {
    void load();
  },
  { immediate: true },
);
</script>

<template>
  <div class="embed" :class="`embed--${entityType}`">
    <div v-if="loading" class="embed__state">
      <AppIcon name="spinner" class="embed__spinner" />
    </div>

    <div v-else-if="failed" class="embed__state">
      {{ t("embed.unavailable") }}
    </div>

    <template v-else>
      <div class="embed__header">
        <img v-if="coverUrl" :src="coverUrl" class="embed__cover" alt="" />
        <div class="embed__titles">
          <a
            :href="pageUrl ?? undefined"
            target="_blank"
            rel="noopener"
            class="embed__title"
          >
            {{ title }}
          </a>
          <span v-if="subtitle" class="embed__subtitle">{{ subtitle }}</span>
          <span v-if="entityType !== 'track'" class="embed__subtitle">
            {{ t("browse.detail.trackCount", tracks.length) }}
          </span>
        </div>
        <a
          v-if="pageUrl"
          :href="pageUrl"
          target="_blank"
          rel="noopener"
          class="embed__open"
          :title="t('embed.openIn', { name: instanceStore.name })"
        >
          <AppIcon name="arrow-up-right-from-square" />
        </a>
      </div>

      <template v-if="entityType === 'track'">
        <audio
          v-if="audioSrc"
          class="embed__audio"
          controls
          preload="none"
          :src="audioSrc"
        ></audio>
        <p v-else class="embed__state">{{ t("embed.unavailable") }}</p>
      </template>

      <ul v-else class="embed__tracks">
        <li v-for="(item, index) in tracks" :key="item.id" class="embed__track">
          <button
            type="button"
            class="embed__track-row"
            :aria-expanded="expandedId === item.id"
            @click="toggle(item.id)"
          >
            <span class="embed__track-number">{{ index + 1 }}</span>
            <span class="embed__track-title">{{ item.title }}</span>
            <span v-if="item.artist?.name" class="embed__track-artist">
              {{ item.artist.name }}
            </span>
            <span v-if="item.duration" class="embed__track-duration">
              {{ formatTime(item.duration) }}
            </span>
            <AppIcon
              :name="expandedId === item.id ? 'chevron-up' : 'play'"
              class="embed__track-toggle"
            />
          </button>
          <audio
            v-if="expandedId === item.id && trackAudioSrc(item)"
            class="embed__audio"
            controls
            autoplay
            :src="trackAudioSrc(item) ?? undefined"
          ></audio>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.embed {
  box-sizing: border-box;
  min-height: 100%;
  padding: var(--space-3);
  font-family: inherit;
  color: var(--color-text);
  background: var(--color-surface);
}

.embed__state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 6rem;
  color: var(--color-text-muted);
}

.embed__spinner {
  animation: embed-spin 1s linear infinite;
}

@keyframes embed-spin {
  to {
    transform: rotate(360deg);
  }
}

.embed__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}

.embed__cover {
  width: 3.5rem;
  height: 3.5rem;
  border-radius: var(--radius-md);
  object-fit: cover;
  flex-shrink: 0;
}

.embed__titles {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.embed__title {
  font-weight: 600;
  color: var(--color-text);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.embed__title:hover {
  text-decoration: underline;
}

.embed__subtitle {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.embed__open {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.embed__audio {
  width: 100%;
  height: 2.5rem;
}

.embed__tracks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.embed__track {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: var(--space-2);
  margin-bottom: var(--space-2);
}

.embed__track:last-child {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.embed__track-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-1) var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.embed__track-row:hover {
  background: var(--color-surface-hover);
}

.embed__track-number {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  min-width: 1.5rem;
  text-align: right;
}

.embed__track-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.embed__track-artist {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.embed__track-duration {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  font-variant-numeric: tabular-nums;
}

.embed__track-toggle {
  color: var(--color-text-muted);
}

.embed__track .embed__audio {
  margin-top: var(--space-2);
}
</style>
