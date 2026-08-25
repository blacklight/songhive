<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { usePlayerStore } from "@/stores/player";
import AppButton from "@/components/ui/AppButton.vue";

const { t } = useI18n();
const store = usePlayerStore();

const repeatLabel = computed(() => {
  if (store.repeat === "off") return t("player.repeatOff");
  if (store.repeat === "all") return t("player.repeatAll");
  return t("player.repeatOne");
});

const prevDisabled = computed(() => !store.hasPrev && store.repeat === "off");
const nextDisabled = computed(() => !store.hasNext && store.repeat === "off");
</script>

<template>
  <div
    class="player-controls"
    role="group"
    :aria-label="t('player.playbackControls')"
  >
    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__shuffle"
      :class="{ 'player-controls__shuffle--active': store.shuffle }"
      :aria-label="t('player.shuffle')"
      :aria-pressed="store.shuffle"
      icon="shuffle"
      @click="store.toggleShuffle"
    />

    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__prev"
      :aria-label="t('player.previousTrack')"
      :disabled="prevDisabled"
      icon="backward-step"
      @click="store.prev"
    />

    <AppButton
      variant="ghost"
      size="md"
      class="player-controls__play"
      :aria-label="store.isPlaying ? t('common.pause') : t('common.play')"
      :icon="store.isPlaying ? 'pause' : 'play'"
      @click="store.isPlaying ? store.pause() : store.play()"
    />

    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__next"
      :aria-label="t('player.nextTrack')"
      :disabled="nextDisabled"
      icon="forward-step"
      @click="store.next"
    />

    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__repeat"
      :class="`player-controls__repeat--${store.repeat}`"
      :aria-label="repeatLabel"
      :aria-pressed="store.repeat !== 'off'"
      icon="repeat"
      @click="store.cycleRepeat"
    />
  </div>
</template>

<style scoped>
.player-controls {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
}

.player-controls .player-controls__shuffle,
.player-controls .player-controls__prev,
.player-controls .player-controls__next,
.player-controls .player-controls__repeat {
  font-size: 1.25rem;
}

.player-controls .player-controls__play {
  font-size: 1.75rem;
  padding: var(--space-2);
}

.player-controls__play:hover:not(:disabled),
.player-controls__prev:hover:not(:disabled),
.player-controls__next:hover:not(:disabled) {
  color: var(--color-accent);
}

.player-controls__shuffle--active,
.player-controls__repeat--all,
.player-controls__repeat--one {
  color: var(--color-accent);
  background-color: var(--color-surface-raised);
}

.player-controls__repeat--all,
.player-controls__repeat--one {
  position: relative;
}

.player-controls__repeat--all::after,
.player-controls__repeat--one::after {
  content: "";
  position: absolute;
  bottom: 0.1rem;
  left: 50%;
  transform: translateX(-50%);
  width: 0.25rem;
  height: 0.25rem;
  border-radius: var(--radius-full);
  background-color: currentColor;
}
</style>
