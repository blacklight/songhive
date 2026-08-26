<script setup lang="ts">
import { ref, useTemplateRef, watch, type ComponentPublicInstance } from "vue";
import { useI18n } from "vue-i18n";
import { usePlayerStore } from "@/stores/player";
import AppButton from "@/components/ui/AppButton.vue";
import NowPlaying from "./NowPlaying.vue";
import PlayerControls from "./PlayerControls.vue";
import ProgressBar from "./ProgressBar.vue";
import VolumeControl from "./VolumeControl.vue";
import QueuePanel from "./QueuePanel.vue";
import { useMediaSession } from "@/composables/useMediaSession";

const { t } = useI18n();
const store = usePlayerStore();
const expanded = ref(false);
const queueOpen = ref(false);
const queueToggleRef = useTemplateRef<ComponentPublicInstance>("queueToggle");
const expandedQueueToggleRef = useTemplateRef<ComponentPublicInstance>(
  "expandedQueueToggle",
);

useMediaSession();

function toggleExpanded() {
  expanded.value = !expanded.value;
}

function toggleQueue() {
  queueOpen.value = !queueOpen.value;
}

function closeQueue() {
  queueOpen.value = false;
}

watch(
  () => store.currentTrack,
  () => {
    if (!store.currentTrack) {
      expanded.value = false;
      queueOpen.value = false;
    }
  },
);

function queueReturnTarget() {
  return expanded.value ? expandedQueueToggleRef.value : queueToggleRef.value;
}
</script>

<template>
  <div
    v-if="store.currentTrack"
    class="player-bar"
    :class="{ 'player-bar--expanded': expanded }"
    role="region"
    :aria-label="t('player.player')"
  >
    <QueuePanel
      :open="queueOpen"
      :return-focus-to="queueReturnTarget()"
      @close="closeQueue"
    />

    <div class="player-bar__full">
      <div class="player-bar__left">
        <NowPlaying />
      </div>

      <div class="player-bar__center">
        <PlayerControls />
        <ProgressBar />
      </div>

      <div class="player-bar__right">
        <VolumeControl />
        <AppButton
          ref="queueToggle"
          variant="ghost"
          size="sm"
          class="player-bar__queue-toggle"
          :aria-label="
            queueOpen ? t('player.closeQueue') : t('player.openQueue')
          "
          :title="queueOpen ? t('player.closeQueue') : t('player.openQueue')"
          :aria-pressed="queueOpen"
          icon="list"
          @click="toggleQueue"
        />
      </div>
    </div>

    <div class="player-bar__mini">
      <button
        class="player-bar__mini-info"
        :aria-label="t('player.expandPlayer')"
        @click="toggleExpanded"
      >
        <NowPlaying mini />
      </button>

      <div class="player-bar__mini-controls">
        <AppButton
          variant="ghost"
          size="sm"
          class="player-bar__mini-play"
          :aria-label="store.isPlaying ? t('common.pause') : t('common.play')"
          :title="store.isPlaying ? t('common.pause') : t('common.play')"
          :icon="store.isPlaying ? 'pause' : 'play'"
          @click="store.isPlaying ? store.pause() : store.play()"
        />

        <AppButton
          variant="ghost"
          size="sm"
          class="player-bar__mini-next"
          :aria-label="t('player.nextTrack')"
          :title="t('player.nextTrack')"
          :disabled="!store.hasNext && store.repeat === 'off'"
          icon="forward-step"
          @click="store.next"
        />
      </div>
    </div>

    <div class="player-bar__expanded">
      <div class="player-bar__expanded-header">
        <NowPlaying />
        <AppButton
          variant="ghost"
          size="sm"
          class="player-bar__collapse"
          :aria-label="t('player.collapsePlayer')"
          :title="t('player.collapsePlayer')"
          icon="xmark"
          @click="toggleExpanded"
        />
      </div>

      <div class="player-bar__expanded-body">
        <PlayerControls />
        <ProgressBar />
        <div class="player-bar__expanded-bottom">
          <VolumeControl />
          <AppButton
            ref="expandedQueueToggle"
            variant="ghost"
            size="sm"
            class="player-bar__queue-toggle"
            :aria-label="
              queueOpen ? t('player.closeQueue') : t('player.openQueue')
            "
            :title="queueOpen ? t('player.closeQueue') : t('player.openQueue')"
            :aria-pressed="queueOpen"
            icon="list"
            @click="toggleQueue"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.player-bar {
  --player-bar-height: 5rem;
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: var(--player-bar-height);
  background-color: var(--color-surface);
  color: var(--color-text);
  z-index: var(--z-player);
  transition: height var(--transition-base);
  overflow: hidden;
}

.player-bar--expanded {
  --player-bar-height: 13rem;
}

.player-bar__full,
.player-bar__mini {
  display: flex;
  align-items: center;
  height: var(--player-bar-height, 5rem);
  padding: 0 var(--space-4);
  gap: var(--space-4);
}

.player-bar__full {
  display: grid;
  grid-template-columns: 1fr 2fr 1fr;
  align-items: center;
}

.player-bar__left,
.player-bar__right {
  display: flex;
  align-items: center;
  min-width: 0;
}

.player-bar__left {
  justify-content: flex-start;
}

.player-bar__center {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  min-width: 0;
}

.player-bar__right {
  justify-content: flex-end;
  gap: var(--space-2);
}

.player-bar__mini {
  display: none;
  justify-content: space-between;
  padding: 0 var(--space-3);
}

.player-bar__mini-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  padding: 0;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.player-bar__mini-info:hover {
  color: var(--color-accent);
}

.player-bar__mini-controls {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.player-bar__expanded {
  display: none;
  flex-direction: column;
  height: 100%;
  padding: var(--space-2) var(--space-3) var(--space-3);
}

.player-bar__expanded-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--player-bar-height, 5rem);
  padding: 0 var(--space-3);
}

.player-bar__expanded-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  flex: 1;
  padding: 0 var(--space-4);
}

.player-bar__expanded-bottom {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  max-width: 24rem;
  justify-content: center;
}

.player-bar__expanded-bottom .volume-control {
  flex: 1;
  width: auto;
}

.player-bar .player-bar__queue-toggle,
.player-bar .player-bar__collapse,
.player-bar .player-bar__mini-play,
.player-bar .player-bar__mini-next {
  font-size: 1.25rem;
}

@media (max-width: 767px) {
  .player-bar__full {
    display: none;
  }

  .player-bar__mini {
    display: flex;
  }

  .player-bar--expanded .player-bar__mini,
  .player-bar--expanded .player-bar__full {
    display: none;
  }

  .player-bar--expanded .player-bar__expanded {
    display: flex;
  }

  .player-bar__queue-toggle,
  .player-bar__collapse {
    flex-shrink: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .player-bar {
    transition: none;
  }
}
</style>
