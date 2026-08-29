<script setup lang="ts">
import AppIcon from "@/components/ui/AppIcon.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

export interface Props {
  label: string;
  value: string | number;
  icon?: string;
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  icon: undefined,
  loading: false,
});
</script>

<template>
  <div class="stat-card">
    <div class="stat-card__header">
      <AppIcon v-if="props.icon" :name="props.icon" class="stat-card__icon" />
      <span class="stat-card__label">{{ props.label }}</span>
    </div>
    <SkeletonLoader
      v-if="props.loading"
      variant="card"
      class="stat-card__skeleton"
    />
    <span v-else class="stat-card__value">{{ props.value }}</span>
  </div>
</template>

<style scoped>
.stat-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
}

.stat-card__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
}

.stat-card__icon {
  font-size: 0.875rem;
}

.stat-card__label {
  font-size: 0.875rem;
  font-weight: 500;
}

.stat-card__value {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--color-text);
}

.stat-card__skeleton {
  height: 2rem;
  border-radius: var(--radius-md);
}
</style>
