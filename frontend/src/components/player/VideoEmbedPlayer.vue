<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";

/**
 * Reusable embedded video player.
 *
 * Renders a responsive 16:9 ``<video>`` element for any same-origin or
 * proxied media URL — currently used by the YouTube "watch video" action
 * and intended for other video use cases (promo clips, music videos).
 */

const props = withDefaults(
  defineProps<{
    src: string;
    title?: string;
    poster?: string | null;
    autoplay?: boolean;
  }>(),
  {
    title: "",
    poster: null,
    autoplay: true,
  },
);

const emit = defineEmits<{
  close: [];
}>();

const { t } = useI18n();

const videoEl = ref<HTMLVideoElement | null>(null);

watch(
  () => props.src,
  () => {
    videoEl.value?.load();
  },
);

function onClose() {
  videoEl.value?.pause();
  emit("close");
}
</script>

<template>
  <div class="video-embed-player">
    <div class="video-embed-player__frame">
      <video
        ref="videoEl"
        class="video-embed-player__video"
        :src="src"
        :poster="poster ?? undefined"
        :title="title"
        controls
        playsinline
        preload="metadata"
        :autoplay="autoplay"
      />
      <button
        type="button"
        class="video-embed-player__close"
        :aria-label="t('common.close')"
        @click="onClose"
      >
        ×
      </button>
    </div>
  </div>
</template>

<style scoped>
.video-embed-player {
  width: 100%;
}

.video-embed-player__frame {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background-color: #000;
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.video-embed-player__video {
  width: 100%;
  height: 100%;
  display: block;
}

.video-embed-player__close {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: 2rem;
  height: 2rem;
  border: none;
  border-radius: var(--radius-full);
  background-color: rgb(0 0 0 / 60%);
  color: #fff;
  font-size: 1.25rem;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.15s ease-in-out;
}

.video-embed-player__frame:hover .video-embed-player__close,
.video-embed-player__close:focus-visible {
  opacity: 1;
}
</style>
