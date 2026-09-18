<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ApiError, getApiErrorMessage } from "@/api/client";
import { getAlbumStats } from "@/api/albums";
import { getArtistStats } from "@/api/artists";
import { getPlaylistStats, listPlaylistTracks } from "@/api/playlists";
import { getLibraryStats, listLibraryTracks } from "@/api/libraries";
import { getTrack, listTracks, type TrackResponse } from "@/api/tracks";
import type { ShareItemType } from "@/api/shares";
import { useToastStore } from "@/stores/toast";
import { getPublicUrl } from "@/utils/share";
import {
  audioEmbed,
  EMBED_LIST_MAX_TRACKS,
  iframeEmbed,
  markdownEmbed,
  scriptEmbed,
  trackLinkText,
  trackListEmbed,
  type EmbedFormat,
} from "@/utils/embed";
import AppButton from "@/components/ui/AppButton.vue";
import AppSelect from "@/components/ui/AppSelect.vue";

export interface Props {
  itemType: ShareItemType;
  itemId: string;
  title: string;
  /** Direct media download URL for tracks (the track ``audio_url``). */
  downloadUrl?: string | null;
  /** Whether the entity is publicly reachable (required for embeds). */
  isPublic: boolean;
}

const props = defineProps<Props>();

const { t } = useI18n();
const toast = useToastStore();

const pageUrl = computed(
  () => getPublicUrl(props.itemType, props.itemId) ?? "",
);

const track = ref<TrackResponse | null>(null);
const collectionTrackCount = ref<number | null>(null);
const collectionTracks = ref<TrackResponse[] | null>(null);
const loading = ref(false);
const listLoading = ref(false);
const loadError = ref<string | null>(null);

const format = ref<EmbedFormat | "">("");
// Tracks whether the user picked a format; until then the selection follows
// the first available option as async data (track details, track counts)
// resolves.
const userPickedFormat = ref(false);

function onFormatChange(value: string) {
  userPickedFormat.value = true;
  format.value = value as EmbedFormat;
}

// Track embeds prefer the ``audio_url`` the caller already resolved; the
// fetched track is the fallback for call sites that do not pass it.
const audioUrl = computed(
  () => props.downloadUrl || track.value?.audio_url || null,
);

const trackLabel = computed(() =>
  trackLinkText({
    title: track.value?.title ?? props.title,
    filename: track.value?.filename,
    artistName: track.value?.artist?.name,
  }),
);

const listEligible = computed(
  () =>
    collectionTrackCount.value !== null &&
    collectionTrackCount.value < EMBED_LIST_MAX_TRACKS,
);

const formatOptions = computed(() => {
  const options: { value: EmbedFormat; label: string }[] = [];
  if (props.itemType === "track") {
    if (audioUrl.value) {
      options.push({
        value: "audio",
        label: t("browse.share.embedFormatAudio"),
      });
    }
  } else if (listEligible.value) {
    options.push({
      value: "html",
      label: t("browse.share.embedFormatHtmlList"),
    });
  }
  options.push(
    { value: "markdown", label: t("browse.share.embedFormatMarkdown") },
    { value: "script", label: t("browse.share.embedFormatScript") },
    { value: "iframe", label: t("browse.share.embedFormatIframe") },
  );
  return options;
});

watch(
  formatOptions,
  (options) => {
    if (!options.length) return;
    const stillValid = options.some((option) => option.value === format.value);
    if (!userPickedFormat.value || !stillValid) {
      format.value = options[0].value;
    }
  },
  { immediate: true },
);

function errorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return t("pages.forbidden");
  }
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function fetchStats(): Promise<number | null> {
  switch (props.itemType) {
    case "album":
      return (await getAlbumStats(props.itemId)).track_count;
    case "artist":
      return (await getArtistStats(props.itemId)).track_count;
    case "playlist":
      return (await getPlaylistStats(props.itemId)).track_count;
    case "library":
      return (await getLibraryStats(props.itemId)).track_count;
    default:
      return null;
  }
}

function fetchTrackPage(
  offset: number,
  limit: number,
): Promise<TrackResponse[]> {
  const query = { limit, offset, include: "artist,album" };
  switch (props.itemType) {
    case "album":
      return listTracks({ album_id: props.itemId, ...query });
    case "artist":
      return listTracks({ artist_id: props.itemId, ...query });
    case "playlist":
      return listPlaylistTracks(props.itemId, query);
    case "library":
      return listLibraryTracks(props.itemId, query);
    default:
      return Promise.resolve([]);
  }
}

const PAGE_SIZE = 100;

async function loadCollectionTracks() {
  if (collectionTracks.value !== null || listLoading.value) return;
  listLoading.value = true;
  loadError.value = null;
  try {
    const collected: TrackResponse[] = [];
    while (collected.length < EMBED_LIST_MAX_TRACKS) {
      const page = await fetchTrackPage(collected.length, PAGE_SIZE);
      collected.push(...page);
      if (page.length < PAGE_SIZE) break;
    }
    collectionTracks.value = collected;
  } catch (err) {
    loadError.value = errorMessage(err);
  } finally {
    listLoading.value = false;
  }
}

async function loadInfo() {
  loading.value = true;
  loadError.value = null;
  try {
    if (props.itemType === "track") {
      track.value = await getTrack(props.itemId, {
        include: "artist,album",
      });
    } else {
      collectionTrackCount.value = await fetchStats();
    }
  } catch (err) {
    loadError.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

watch(
  () => format.value,
  (value) => {
    if (value === "html") void loadCollectionTracks();
  },
);

watch(
  () => [props.itemType, props.itemId],
  () => {
    track.value = null;
    collectionTrackCount.value = null;
    collectionTracks.value = null;
    loadError.value = null;
    userPickedFormat.value = false;
    format.value = formatOptions.value[0]?.value ?? "";
    if (props.isPublic) void loadInfo();
  },
  { immediate: true },
);

// Only public tracks with a playable file may leave the instance — a private
// or external track in a public collection would render a dead player.
const embeddableTracks = computed(() =>
  (collectionTracks.value ?? []).filter(
    (item) => item.visibility === "public" && item.audio_url,
  ),
);

const sortedCollectionTracks = computed(() => {
  const items = [...embeddableTracks.value];
  if (props.itemType === "album") {
    items.sort(
      (a, b) =>
        (a.disc_number ?? 0) - (b.disc_number ?? 0) ||
        (a.track_number ?? 0) - (b.track_number ?? 0),
    );
  }
  return items;
});

const embedCode = computed(() => {
  switch (format.value) {
    case "audio":
      if (!audioUrl.value) return "";
      return audioEmbed({
        pageUrl: pageUrl.value,
        audioUrl: audioUrl.value,
        label: trackLabel.value || props.title,
      });
    case "html":
      if (collectionTracks.value === null) return "";
      return trackListEmbed({
        pageUrl: pageUrl.value,
        title: props.title,
        tracks: sortedCollectionTracks.value.map((item) => ({
          label: trackLinkText({
            title: item.title,
            filename: item.filename,
            artistName: item.artist?.name,
          }),
          audioUrl: item.audio_url!,
        })),
      });
    case "markdown":
      return markdownEmbed(
        props.itemType === "track"
          ? trackLabel.value || props.title
          : props.title,
        pageUrl.value,
      );
    case "script":
      return scriptEmbed(props.itemType, props.itemId);
    case "iframe":
      return iframeEmbed(props.itemType, props.itemId, props.title);
    default:
      return "";
  }
});

async function copyCode() {
  try {
    await navigator.clipboard.writeText(embedCode.value);
    toast.push({ type: "success", message: t("browse.share.urlCopied") });
  } catch {
    toast.push({ type: "error", message: t("browse.share.copyFailed") });
  }
}
</script>

<template>
  <div class="embed-panel">
    <p v-if="!props.isPublic" class="embed-panel__hint">
      {{ t("browse.share.embedNotPublic") }}
    </p>

    <template v-else>
      <p class="embed-panel__hint">{{ t("browse.share.embedHint") }}</p>

      <AppSelect
        :model-value="format"
        :options="formatOptions"
        :label="t('browse.share.embedFormat')"
        :disabled="loading"
        @update:model-value="onFormatChange"
      />

      <div v-if="loadError" class="embed-panel__error" role="alert">
        {{ loadError }}
        <AppButton size="sm" icon="rotate-right" @click="loadInfo">
          {{ t("common.retry") }}
        </AppButton>
      </div>

      <div class="embed-panel__code-box">
        <pre
          class="embed-panel__code"
        ><code>{{ loading || listLoading ? t("browse.share.embedLoading") : embedCode }}</code></pre>
        <AppButton
          size="sm"
          icon="copy"
          :disabled="!embedCode"
          @click="copyCode"
        >
          {{ t("common.copy") }}
        </AppButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.embed-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.embed-panel__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.embed-panel__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.embed-panel__code-box {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-secondary);
}

.embed-panel__code {
  margin: 0;
  max-height: 12rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 0.8125rem;
}
</style>
