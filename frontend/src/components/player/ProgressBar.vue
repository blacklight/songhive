<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { usePlayerStore } from "@/stores/player";
import { formatTime } from "@/utils/time";
import AppSlider from "@/components/ui/AppSlider.vue";

const { t } = useI18n();
const store = usePlayerStore();

const displayValue = computed(() =>
  Math.min(store.currentTime, store.duration || store.currentTime),
);

const valueText = computed(() =>
  t("player.progressValue", {
    current: formatTime(store.currentTime),
    duration: formatTime(store.duration),
  }),
);

function onInput(seconds: number) {
  store.setDisplayedTime(seconds);
}

function onChange(seconds: number) {
  store.seek(seconds);
}

function onKeyDown(event: KeyboardEvent) {
  const step = 5;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    store.seek(store.currentTime - step);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    store.seek(store.currentTime + step);
  } else if (event.key === "Home") {
    event.preventDefault();
    store.seek(0);
  } else if (event.key === "End") {
    event.preventDefault();
    store.seek(store.duration || 0);
  }
}
</script>

<template>
  <div class="progress-bar" role="group" :aria-label="t('player.progress')">
    <time class="progress-bar__time" aria-hidden="true">
      {{ formatTime(store.currentTime) }}
    </time>
    <AppSlider
      ref="slider"
      class="progress-bar__slider"
      :model-value="displayValue"
      :min="0"
      :max="store.duration || 0"
      :step="0.1"
      :aria-label="t('player.seek')"
      :aria-value-text="valueText"
      @update:model-value="onInput"
      @change="onChange"
      @keydown="onKeyDown"
    />
    <time class="progress-bar__time" aria-hidden="true">
      {{ formatTime(store.duration) }}
    </time>
  </div>
</template>

<style scoped>
.progress-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
}

.progress-bar__time {
  flex-shrink: 0;
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  color: var(--color-text-muted);
  min-width: 2.5rem;
  text-align: center;
}

.progress-bar__slider {
  flex: 1;
  min-width: 0;
}
</style>
