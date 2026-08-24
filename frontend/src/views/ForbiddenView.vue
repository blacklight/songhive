<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const { t } = useI18n();
const authStore = useAuthStore();

const redirectTarget = computed(() =>
  authStore.isAuthenticated ? "/" : "/login",
);
const redirectLabel = computed(() =>
  authStore.isAuthenticated ? t("pages.goHome") : t("pages.goLogin"),
);
</script>

<template>
  <main class="error-view">
    <h1>403</h1>
    <p>{{ t("pages.forbidden") }}</p>
    <RouterLink :to="redirectTarget" class="error-view__link">
      {{ redirectLabel }}
    </RouterLink>
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
