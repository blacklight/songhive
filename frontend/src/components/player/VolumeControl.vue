<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { usePlayerStore } from "@/stores/player";
import AppButton from "@/components/ui/AppButton.vue";
import AppSlider from "@/components/ui/AppSlider.vue";

const { t } = useI18n();
const store = usePlayerStore();

const volumePercent = computed(() => Math.round(store.volume * 100));

const valueText = computed(() =>
  t("player.volumeValue", {
    value: store.muted ? 0 : volumePercent.value,
  }),
);

function onVolumeChange(value: number) {
  store.setVolume(value);
  if (store.muted && value > 0) {
    store.toggleMute();
  }
}
</script>

<template>
  <div
    class="volume-control"
    role="group"
    :aria-label="t('player.volumeControls')"
  >
    <AppButton
      variant="ghost"
      size="sm"
      class="volume-control__mute"
      :aria-label="store.muted ? t('player.unmute') : t('player.mute')"
      :title="store.muted ? t('player.unmute') : t('player.mute')"
      :icon="
        store.muted || store.volume === 0
          ? 'volume-xmark'
          : store.volume < 0.5
            ? 'volume-low'
            : 'volume-high'
      "
      @click="store.toggleMute"
    />

    <AppSlider
      class="volume-control__slider"
      :model-value="store.muted ? 0 : store.volume"
      :min="0"
      :max="1"
      :step="0.01"
      :aria-label="t('player.volume')"
      :aria-value-text="valueText"
      @update:model-value="onVolumeChange"
    />
  </div>
</template>

<style scoped>
.volume-control {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 8rem;
  min-width: 0;
}

.volume-control .volume-control__mute {
  font-size: 1.25rem;
}

.volume-control__slider {
  flex: 1;
  min-width: 0;
}
</style>
