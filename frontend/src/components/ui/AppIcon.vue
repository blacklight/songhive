<script setup lang="ts">
import { computed } from "vue";

export interface Props {
  name: string;
  title?: string;
  variant?: "solid" | "regular" | "light" | "brand";
  spacing?: "left" | "right" | "none";
}

const props = withDefaults(defineProps<Props>(), {
  variant: "solid",
  spacing: "none",
  title: "",
});

const variantClass = computed(() => {
  if (props.variant === "brand") return "fa-brands";
  return `fa-${props.variant}`;
});
</script>

<template>
  <i
    :class="[
      variantClass,
      `fa-${props.name}`,
      {
        'app-icon--spaced-right': props.spacing === 'right',
        'app-icon--spaced-left': props.spacing === 'left',
      },
    ]"
    :title="props.title"
    aria-hidden="true"
    :aria-label="props.title"
  ></i>
</template>

<style scoped>
.app-icon--spaced-right {
  margin-right: var(--space-2);
}

.app-icon--spaced-left {
  margin-left: var(--space-2);
}
</style>
