<script setup lang="ts">
import { computed } from "vue";
import { ref } from "vue";

export interface Props {
  src?: string;
  name: string;
  size?: "sm" | "md" | "lg";
  width?: string;
}

const props = withDefaults(defineProps<Props>(), {
  size: "md",
});

const hasError = ref(false);

const sizeClasses = {
  sm: "app-avatar--sm",
  md: "app-avatar--md",
  lg: "app-avatar--lg",
};

const hasAvatar = computed(() => !!props.src && !hasError.value);

const classes = computed(() => {
  let classes = ["app-avatar"];
  if (!hasAvatar.value) {
    classes.push("app-avatar--initials");
  }

  if (props.width) {
    return classes;
  }

  return ["app-avatar", sizeClasses[props.size] || sizeClasses.md];
});

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
    v-if="hasAvatar"
    :src="props.src"
    :alt="props.name"
    :class="classes"
    :width="props.width"
    :height="props.width"
    @error="onError"
  />
  <div
    v-else
    :class="classes"
    :style="{ width: props.width, height: props.width }"
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
  border-radius: var(--radius-full);
  object-fit: cover;
  background-color: var(--color-surface-raised);
  color: var(--color-accent-contrast);
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
