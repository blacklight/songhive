<script setup lang="ts">
import { ref } from "vue";

export interface Props {
  src?: string;
  name: string;
  size?: "sm" | "md" | "lg";
}

const props = withDefaults(defineProps<Props>(), {
  size: "md",
});

const hasError = ref(false);

const sizeClass = {
  sm: "app-avatar--sm",
  md: "app-avatar--md",
  lg: "app-avatar--lg",
};

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function onError() {
  hasError.value = true;
}
</script>

<template>
  <img
    v-if="props.src && !hasError"
    :src="props.src"
    :alt="props.name"
    :class="['app-avatar', sizeClass[props.size]]"
    @error="onError"
  />
  <div
    v-else
    :class="['app-avatar', 'app-avatar--initials', sizeClass[props.size]]"
  >
    {{ initials(props.name) }}
  </div>
</template>

<style scoped>
.app-avatar,
.app-avatar--initials {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-right: var(--space-3);
  margin-bottom: var(--space-2);
  border-radius: var(--radius-full);
  object-fit: cover;
  background-color: var(--color-surface-raised);
  color: var(--color-text);
  font-weight: 600;
}

.app-avatar--sm {
  width: 2rem;
  height: 2rem;
  font-size: 0.75rem;
}

.app-avatar--md {
  width: 2.5rem;
  height: 2.5rem;
  font-size: 1rem;
}

.app-avatar--lg {
  width: 3.5rem;
  height: 3.5rem;
  font-size: 1.25rem;
}

.app-avatar--initials {
  border: 1px solid var(--color-border);
}
</style>
