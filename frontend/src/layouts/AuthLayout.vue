<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { RouterLink, RouterView } from "vue-router";
import { useInstanceStore } from "@/stores/instance";
import AppToast from "@/components/feedback/AppToast.vue";

const { t } = useI18n();
const instanceStore = useInstanceStore();
</script>

<template>
  <div class="auth-layout">
    <div class="auth-layout__card">
      <RouterLink
        to="/"
        class="auth-layout__brand"
        :aria-label="t('pages.goHome')"
      >
        <img src="/logo.png" alt="" class="auth-layout__logo" />
        <span class="auth-layout__brand-name">{{ instanceStore.name }}</span>
      </RouterLink>
      <RouterView />
    </div>
  </div>
  <AppToast />
</template>

<style scoped>
.auth-layout {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  background-color: var(--color-bg);
  color: var(--color-text);
}

.auth-layout__card {
  width: 100%;
  max-width: 26.25rem;
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  box-shadow: var(--shadow-md);
}

.auth-layout__brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  margin: 0 0 var(--space-6);
  color: var(--color-text);
  text-decoration: none;
  font-size: 1.5rem;
  font-weight: 600;
}

.auth-layout__brand:hover {
  filter: brightness(0.95);
}

.auth-layout__logo {
  width: 6rem;
  height: 6rem;
}
</style>
