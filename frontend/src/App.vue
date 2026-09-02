<script setup lang="ts">
import { onMounted } from "vue";
import { RouterView } from "vue-router";
import ConfirmDialog from "@/components/feedback/ConfirmDialog.vue";
import { usePwa } from "@/composables/usePwa";

const { registerServiceWorker } = usePwa();

onMounted(() => {
  registerServiceWorker();
});
</script>

<template>
  <RouterView />
  <ConfirmDialog />
</template>

<style>
html,
body {
  scroll-padding-bottom: calc(5rem + var(--space-4));
}

body {
  font-family:
    -apple-system, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Fira Sans",
    "Open Sans", "Droid Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
  letter-spacing: 0.0333em;
  margin: 0;
}

main section.boxed {
  border: 1px solid var(--color-border);
}
</style>
