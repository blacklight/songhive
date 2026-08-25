<script setup lang="ts">
import { computed } from "vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppIcon from "./AppIcon.vue";

export interface Props {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
  icon?: string;
  iconVariant?: "solid" | "regular" | "light" | "brand";
  title?: string;
  ariaLabel?: string;
}

const props = withDefaults(defineProps<Props>(), {
  variant: "primary",
  size: "md",
  type: "button",
  iconVariant: "solid",
  title: undefined,
  ariaLabel: undefined,
});

const accessibleLabel = computed(() => props.ariaLabel ?? props.title);
</script>

<template>
  <button
    :type="props.type"
    :class="['app-btn', `app-btn--${props.variant}`, `app-btn--${props.size}`]"
    :title="props.title"
    :aria-label="accessibleLabel"
    :disabled="props.loading || props.disabled"
    :aria-busy="props.loading ? 'true' : 'false'"
  >
    <AppIcon
      v-if="props.icon && !props.loading"
      :name="props.icon"
      :variant="props.iconVariant"
    />
    <AppSpinner v-if="props.loading" size="sm" class="app-btn__spinner" />
    <slot />
  </button>
</template>

<style scoped>
.app-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-weight: 500;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast);
}

.app-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.app-btn--sm {
  padding: var(--space-2);
  font-size: 0.875rem;
}

.app-btn--md {
  padding: var(--space-2) var(--space-3);
  font-size: 1rem;
}

.app-btn--lg {
  padding: var(--space-3) var(--space-4);
  font-size: 1.125rem;
}

.app-btn--primary {
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
}

.app-btn--primary:hover:not(:disabled) {
  filter: brightness(0.95);
}

.app-btn--secondary {
  background-color: var(--color-surface);
  color: var(--color-text);
  border-color: var(--color-border);
}

.app-btn--secondary:hover:not(:disabled) {
  background-color: var(--color-surface-raised);
}

.app-btn--ghost {
  background-color: transparent;
  color: var(--color-text);
}

.app-btn--ghost:hover:not(:disabled) {
  background-color: var(--color-surface-raised);
}

.app-btn--danger {
  background-color: var(--color-danger);
  color: #fff;
}

.app-btn--danger:hover:not(:disabled) {
  filter: brightness(0.9);
}

.app-btn__spinner {
  color: currentColor;
}
</style>
