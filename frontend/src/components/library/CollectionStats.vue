<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { formatTime } from "@/utils/time";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  trackCount: number;
  albumCount?: number | null;
  totalDuration?: number | null;
}

withDefaults(defineProps<Props>(), {
  albumCount: null,
  totalDuration: null,
});

const { t } = useI18n();
</script>

<template>
  <span class="collection-stats">
    <span v-if="albumCount !== null" class="collection-stats__item">
      <AppIcon name="compact-disc" />
      {{ t("browse.detail.albumCount", albumCount) }}
    </span>
    <span class="collection-stats__item">
      <AppIcon name="music" />
      {{ t("browse.detail.trackCount", trackCount) }}
    </span>
    <span v-if="totalDuration" class="collection-stats__item">
      <AppIcon name="clock" />
      {{ formatTime(totalDuration) }}
    </span>
  </span>
</template>

<style scoped>
.collection-stats {
  display: inline-flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.collection-stats__item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
