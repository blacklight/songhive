<script setup lang="ts">
import { computed } from "vue";
import { usePlayerStore } from "@/stores/player";
import AppButton from "@/components/ui/AppButton.vue";

const store = usePlayerStore();

const prevDisabled = computed(
  () => !store.hasPrev && store.repeat === "off",
);
const nextDisabled = computed(
  () => !store.hasNext && store.repeat === "off",
);
</script>

<template>
  <div class="player-controls" role="group" aria-label="Playback controls">
    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__shuffle"
      :class="{ 'player-controls__shuffle--active': store.shuffle }"
      aria-label="Shuffle"
      :aria-pressed="store.shuffle"
      @click="store.toggleShuffle"
    >
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <path
          d="M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.53 2.53L4 19.59 5.41 21 17.96 8.46 20.5 11V4h-6zM14.83 13.41l-1.42 1.41 3.18 3.18L14.5 20h6v-6l-2.53 2.53-3.18-3.18v.06zM5.41 16l1.42-1.41L5.41 13.17 4 14.59V16h1.41z"
        />
      </svg>
    </AppButton>

    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__prev"
      aria-label="Previous track"
      :disabled="prevDisabled"
      @click="store.prev"
    >
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <path d="M6 6h2v12H6zm3.5 6 8.5 6V6z" />
      </svg>
    </AppButton>

    <AppButton
      variant="ghost"
      size="md"
      class="player-controls__play"
      :aria-label="store.isPlaying ? 'Pause' : 'Play'"
      @click="store.isPlaying ? store.pause() : store.play()"
    >
      <svg
        v-if="store.isPlaying"
        viewBox="0 0 24 24"
        width="28"
        height="28"
        aria-hidden="true"
      >
        <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
      </svg>
      <svg
        v-else
        viewBox="0 0 24 24"
        width="28"
        height="28"
        aria-hidden="true"
      >
        <path d="M8 5v14l11-7z" />
      </svg>
    </AppButton>

    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__next"
      aria-label="Next track"
      :disabled="nextDisabled"
      @click="store.next"
    >
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" />
      </svg>
    </AppButton>

    <AppButton
      variant="ghost"
      size="sm"
      class="player-controls__repeat"
      :class="`player-controls__repeat--${store.repeat}`"
      :aria-label="
        store.repeat === 'off'
          ? 'Repeat off'
          : store.repeat === 'all'
            ? 'Repeat all'
            : 'Repeat one'
      "
      :aria-pressed="store.repeat !== 'off'"
      @click="store.cycleRepeat"
    >
      <svg
        v-if="store.repeat === 'off'"
        viewBox="0 0 24 24"
        width="20"
        height="20"
        aria-hidden="true"
      >
        <path
          d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"
        />
      </svg>
      <svg
        v-else-if="store.repeat === 'all'"
        viewBox="0 0 24 24"
        width="20"
        height="20"
        aria-hidden="true"
      >
        <path
          d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"
        />
      </svg>
      <svg
        v-else
        viewBox="0 0 24 24"
        width="20"
        height="20"
        aria-hidden="true"
      >
        <path
          d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"
        />
        <circle cx="12" cy="13" r="3" fill="currentColor" />
      </svg>
    </AppButton>
  </div>
</template>

<style scoped>
.player-controls {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
}

.player-controls__play {
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
