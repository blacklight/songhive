<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";

const { t } = useI18n();
</script>

<template>
  <main class="error-view">
    <h1>404</h1>
    <p>{{ t("pages.notFound") }}</p>
    <RouterLink to="/" class="error-view__link">{{
      t("pages.goHome")
    }}</RouterLink>
  </main>
</template>

<style scoped>
.error-view {
  padding: var(--space-8);
  text-align: center;
  color: var(--color-text);
}

.error-view h1 {
  font-size: 3rem;
  margin-bottom: var(--space-2);
}

.error-view p {
  margin-bottom: var(--space-4);
}

.error-view__link {
  display: inline-block;
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
  text-decoration: none;
  font-weight: 500;
}
</style>
