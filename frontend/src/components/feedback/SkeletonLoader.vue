<script setup lang="ts">
export interface Props {
  variant?: "card" | "list-row" | "page";
}

const props = withDefaults(defineProps<Props>(), {
  variant: "list-row",
});
</script>

<template>
  <div :class="['skeleton', `skeleton--${props.variant}`]" aria-hidden="true">
    <div v-if="props.variant === 'card'" class="skeleton__card">
      <div class="skeleton__line skeleton__line--lg" />
      <div class="skeleton__line skeleton__line--sm" />
    </div>
    <div v-else-if="props.variant === 'list-row'" class="skeleton__row">
      <div class="skeleton__thumb" />
      <div class="skeleton__text">
        <div class="skeleton__line skeleton__line--md" />
        <div class="skeleton__line skeleton__line--sm" />
      </div>
    </div>
    <div v-else class="skeleton__page">
      <div class="skeleton__line skeleton__line--lg" />
      <div v-for="i in 4" :key="i" class="skeleton__line skeleton__line--md" />
    </div>
  </div>
</template>

<style scoped>
.skeleton {
  --skeleton-bg: var(--color-surface);
  --skeleton-shine: var(--color-surface-raised);
  background-color: var(--skeleton-bg);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.skeleton__card,
.skeleton__row,
.skeleton__page {
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.skeleton__row {
  flex-direction: row;
  align-items: center;
  gap: var(--space-3);
}

.skeleton__thumb {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: var(--radius-sm);
  background: linear-gradient(
    90deg,
    var(--color-surface-raised),
    var(--color-border)
  );
}

.skeleton__text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.skeleton__line {
  height: 0.75rem;
  border-radius: var(--radius-sm);
  background: linear-gradient(
    90deg,
    var(--color-surface-raised),
    var(--color-border)
  );
  background-size: 200% 100%;
  animation: shine 1.5s infinite;
}

.skeleton__line--sm {
  width: 40%;
}

.skeleton__line--md {
  width: 70%;
}

.skeleton__line--lg {
  width: 90%;
  height: 1.25rem;
}

@keyframes shine {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton__line {
    animation: none;
  }
}
</style>
