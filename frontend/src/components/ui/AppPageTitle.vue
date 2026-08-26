<script setup lang="ts">
import AppIcon from "./AppIcon.vue";

export interface Props {
  icon?: string;
  level?: 1 | 2 | 3;
  iconVariant?: "solid" | "regular" | "light" | "brand";
}

withDefaults(defineProps<Props>(), {
  level: 1,
  iconVariant: "solid",
});
</script>

<template>
  <component :is="`h${level}`" class="app-page-title">
    <AppIcon v-if="icon" :name="icon" :variant="iconVariant" spacing="right" />
    <slot />
  </component>
</template>

<style scoped>
.app-page-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  margin: 0;
  word-break: break-word;
}
</style>
