<script setup lang="ts">
export interface Props {
  size?: "sm" | "md" | "lg";
  label?: string;
}

const props = withDefaults(defineProps<Props>(), {
  size: "md",
  label: "Loading",
});

const sizeMap = {
  sm: 16,
  md: 24,
  lg: 32,
};
</script>

<template>
  <span class="app-spinner" role="status" aria-live="polite">
    <svg
      :width="sizeMap[props.size]"
      :height="sizeMap[props.size]"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
      class="app-spinner__svg"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        stroke-width="3"
        opacity="0.25"
      />
      <path
        d="M12 2a10 10 0 0 1 10 10"
        stroke="currentColor"
        stroke-width="3"
        stroke-linecap="round"
      />
    </svg>
    <span class="app-spinner__label">{{ props.label }}</span>
  </span>
</template>

<style scoped>
.app-spinner {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text);
}

.app-spinner__label {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.app-spinner__svg {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-spinner__svg {
    animation: none;
  }
}
</style>
