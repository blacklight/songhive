<script lang="ts">
// Embedded activity players share this registry: starting one pauses the
// rest so a feed never plays two tracks at once.
const inlineAudioElements = new Set<HTMLAudioElement>();

function pauseOtherInlineAudio(current: HTMLAudioElement) {
  for (const el of inlineAudioElements) {
    if (el !== current) el.pause();
  }
}
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { ActivityAttachment } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { downloadTrack, getTrack } from "@/api/tracks";
import { toQueueTrack } from "@/player/enrich";
import type { QueueTrack } from "@/player/types";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppSlider from "@/components/ui/AppSlider.vue";
import { formatTime } from "@/utils/time";
import {
  attachmentToQueueTrack,
  audioAttachmentInfo,
} from "@/utils/audioAttachment";

const props = withDefaults(
  defineProps<{
    attachment: ActivityAttachment;
    /**
     * ``true`` for federated activities: a ``songhive:trackId`` on their
     * attachments refers to the origin instance's track, so the local
     * lookup is skipped and the player handoff streams the media URL
     * directly.
     */
    remote?: boolean;
    /**
     * Author/uploader avatar — the last-resort artwork when the attachment
     * carries no ``image`` (track, album or artist art already resolved
     * server-side).
     */
    avatarUrl?: string;
  }>(),
  { remote: false, avatarUrl: undefined },
);

const { t } = useI18n();
const playerStore = usePlayerStore();
const toast = useToastStore();

const audioEl = ref<HTMLAudioElement | null>(null);
const playing = ref(false);
const currentTime = ref(0);
const duration = ref(0);
const volume = ref(1);
const muted = ref(false);
const imageFailed = ref(false);
const handingOff = ref(false);
const downloading = ref(false);

const info = computed(() => audioAttachmentInfo(props.attachment));
duration.value = info.value.duration ?? 0;

const artworkUrl = computed(() => info.value.imageUrl ?? props.avatarUrl);
const showImage = computed(() => !!artworkUrl.value && !imageFailed.value);
const subtitle = computed(() =>
  [info.value.artist, info.value.album].filter(Boolean).join(" · "),
);
const displayTime = computed(() =>
  Math.min(currentTime.value, duration.value || currentTime.value),
);
const seekValueText = computed(() =>
  t("player.progressValue", {
    current: formatTime(currentTime.value),
    duration: formatTime(duration.value),
  }),
);
const volumeValueText = computed(() =>
  t("player.volumeValue", {
    value: muted.value ? 0 : Math.round(volume.value * 100),
  }),
);
const volumeIcon = computed(() =>
  muted.value || volume.value === 0
    ? "volume-xmark"
    : volume.value < 0.5
      ? "volume-low"
      : "volume-high",
);

// Local attachments link the title to the track page; federated ones get
// the origin instance's page (``songhive:trackUrl``) as an external link.
const localTrackRoute = computed(() =>
  !props.remote && info.value.trackId ? `/tracks/${info.value.trackId}` : null,
);

function toggle() {
  const el = audioEl.value;
  if (!el) return;
  if (el.paused) {
    el.play().catch(() => {
      toast.push({
        type: "error",
        message: t("activities.audio.playError"),
      });
    });
  } else {
    el.pause();
  }
}

function onPlay() {
  playing.value = true;
  const el = audioEl.value;
  if (el) pauseOtherInlineAudio(el);
  // One soundtrack at a time: an inline preview stops the player bar.
  if (playerStore.isPlaying) playerStore.pause();
}

function onTimeUpdate() {
  const el = audioEl.value;
  if (el) currentTime.value = el.currentTime;
}

function onLoadedMetadata() {
  const el = audioEl.value;
  if (el && Number.isFinite(el.duration)) duration.value = el.duration;
}

function onSeekInput(seconds: number) {
  currentTime.value = seconds;
}

function onSeekChange(seconds: number) {
  const el = audioEl.value;
  if (el) el.currentTime = seconds;
  currentTime.value = seconds;
}

function onVolumeChange(value: number) {
  volume.value = Math.min(Math.max(value, 0), 1);
  if (muted.value && volume.value > 0) muted.value = false;
}

function toggleMute() {
  muted.value = !muted.value;
}

watch([volume, muted], ([v, m]) => {
  const el = audioEl.value;
  if (el) {
    el.volume = v;
    el.muted = m;
  }
});

// The global player taking over stops the inline preview.
watch(
  () => playerStore.isPlaying,
  (isPlaying) => {
    const el = audioEl.value;
    if (isPlaying && el && !el.paused) el.pause();
  },
);

async function resolveQueueTrack(): Promise<QueueTrack> {
  const meta = info.value;
  if (!props.remote && meta.trackId) {
    try {
      return toQueueTrack(await getTrack(meta.trackId), {
        artist_name: meta.artist ?? "",
        album_title: meta.album,
        artwork_url: meta.imageUrl,
      });
    } catch {
      // The track is gone or not accessible — the attachment URL still
      // plays as remote audio.
    }
  }
  return attachmentToQueueTrack(meta, props.avatarUrl);
}

async function playInPlayer() {
  if (handingOff.value) return;
  handingOff.value = true;
  try {
    const track = await resolveQueueTrack();
    audioEl.value?.pause();
    playerStore.playTrack(track);
  } finally {
    handingOff.value = false;
  }
}

async function addToQueue() {
  const track = await resolveQueueTrack();
  playerStore.enqueue(track);
  toast.push({
    type: "success",
    message: t("activities.audio.addedToQueue"),
  });
}

// The attachment URL is the direct media link — a same-origin file
// download for local tracks, the origin instance's media URL for
// federated ones (songhive media endpoints allow CORS ``*``).
async function download() {
  if (downloading.value) return;
  downloading.value = true;
  try {
    await downloadTrack(info.value.url, info.value.title);
  } catch (err) {
    toast.push({
      type: "error",
      message: t("activities.audio.downloadError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    downloading.value = false;
  }
}

onMounted(() => {
  const el = audioEl.value;
  if (el) inlineAudioElements.add(el);
});

onBeforeUnmount(() => {
  const el = audioEl.value;
  if (el) {
    el.pause();
    inlineAudioElements.delete(el);
  }
});
</script>

<template>
  <div
    class="audio-player"
    role="group"
    :aria-label="t('activities.audio.player')"
  >
    <audio
      ref="audioEl"
      :src="info.url"
      preload="metadata"
      class="audio-player__el"
      @play="onPlay"
      @pause="playing = false"
      @ended="playing = false"
      @timeupdate="onTimeUpdate"
      @loadedmetadata="onLoadedMetadata"
    />
    <div class="audio-player__artwork">
      <img
        v-if="showImage"
        :src="artworkUrl"
        :alt="info.title ?? ''"
        class="audio-player__artwork-img"
        loading="lazy"
        @error="imageFailed = true"
      />
      <AppIcon v-else name="music" />
    </div>
    <div class="audio-player__body">
      <div class="audio-player__meta">
        <RouterLink
          v-if="localTrackRoute"
          :to="localTrackRoute"
          class="audio-player__title"
          :title="info.title"
          >{{ info.title }}</RouterLink
        >
        <a
          v-else-if="info.trackUrl"
          :href="info.trackUrl"
          target="_blank"
          rel="noopener"
          class="audio-player__title"
          :title="info.title"
          >{{ info.title }}</a
        >
        <span v-else class="audio-player__title" :title="info.title">{{
          info.title
        }}</span>
        <span
          v-if="subtitle"
          class="audio-player__subtitle"
          :title="subtitle"
          >{{ subtitle }}</span
        >
      </div>
      <div class="audio-player__controls">
        <AppButton
          variant="ghost"
          size="sm"
          class="audio-player__play"
          :icon="playing ? 'pause' : 'play'"
          :aria-label="playing ? t('common.pause') : t('common.play')"
          :title="playing ? t('common.pause') : t('common.play')"
          @click="toggle"
        />
        <div class="audio-player__progress">
          <time class="audio-player__time" aria-hidden="true">{{
            formatTime(displayTime)
          }}</time>
          <AppSlider
            class="audio-player__seek"
            :model-value="displayTime"
            :min="0"
            :max="duration || 0"
            :step="0.1"
            :aria-label="t('player.seek')"
            :aria-value-text="seekValueText"
            @update:model-value="onSeekInput"
            @change="onSeekChange"
          />
          <time class="audio-player__time" aria-hidden="true">{{
            formatTime(duration)
          }}</time>
        </div>
        <AppButton
          variant="ghost"
          size="sm"
          class="audio-player__mute"
          :icon="volumeIcon"
          :aria-label="muted ? t('player.unmute') : t('player.mute')"
          :title="muted ? t('player.unmute') : t('player.mute')"
          @click="toggleMute"
        />
        <AppSlider
          class="audio-player__volume"
          :model-value="muted ? 0 : volume"
          :min="0"
          :max="1"
          :step="0.01"
          :aria-label="t('player.volume')"
          :aria-value-text="volumeValueText"
          @update:model-value="onVolumeChange"
        />
        <AppButton
          variant="ghost"
          size="sm"
          class="audio-player__action"
          icon="circle-play"
          :loading="handingOff"
          :title="t('activities.audio.playInPlayer')"
          :aria-label="t('activities.audio.playInPlayer')"
          @click="playInPlayer"
        />
        <AppButton
          variant="ghost"
          size="sm"
          class="audio-player__action"
          icon="square-plus"
          :title="t('activities.audio.addToQueue')"
          :aria-label="t('activities.audio.addToQueue')"
          @click="addToQueue"
        />
        <AppButton
          variant="ghost"
          size="sm"
          class="audio-player__action"
          icon="download"
          :loading="downloading"
          :title="t('common.download')"
          :aria-label="t('common.download')"
          @click="download"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.audio-player {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
  max-width: 36rem;
}

.audio-player__el {
  display: none;
}

.audio-player__artwork {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 3.5rem;
  height: 3.5rem;
  border-radius: var(--radius-md);
  overflow: hidden;
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 1.25rem;
}

.audio-player__artwork-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.audio-player__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  flex: 1;
}

.audio-player__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.audio-player__title {
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
}

a.audio-player__title:hover,
a.audio-player__title:focus-visible {
  text-decoration: underline;
}

.audio-player__subtitle {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audio-player__controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.audio-player__play {
  font-size: 1.1rem;
}

.audio-player__time {
  flex-shrink: 0;
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-muted);
}

.audio-player__progress {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  min-width: 0;
}

.audio-player__seek {
  flex: 1;
  min-width: 0;
}

.audio-player__volume {
  width: 5rem;
  min-width: 0;
}

.audio-player__action {
  color: var(--color-text-muted);
}

@media (max-width: 767px) {
  .audio-player {
    gap: var(--space-2);
    padding: var(--space-2);
  }

  .audio-player__artwork {
    width: 2.75rem;
    height: 2.75rem;
  }

  .audio-player__controls {
    flex-wrap: wrap;
  }

  /* The progress bar wraps to its own full-width row so the seek slider
     keeps a usable width instead of collapsing between the buttons. */
  .audio-player__progress {
    order: 2;
    flex-basis: 100%;
  }

  .audio-player__volume {
    display: none;
  }
}
</style>
