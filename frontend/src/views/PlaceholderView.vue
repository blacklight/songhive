<script setup lang="ts">
import { useI18n } from "vue-i18n";

export interface Props {
  name: string;
  phase: number;
}

const props = defineProps<Props>();
const { t } = useI18n();
</script>

<template>
  <main class="placeholder-view">
    <h1>
      {{ t("pages.placeholder", { name: props.name, phase: props.phase }) }}
    </h1>
  </main>
</template>

<style scoped>
.placeholder-view {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}
</style>
