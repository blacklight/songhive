<script setup lang="ts">
import { RouterView } from "vue-router";
import AppToast from "@/components/feedback/AppToast.vue";
</script>

<template>
  <div class="auth-layout">
    <div class="auth-layout__card">
      <h1 class="auth-layout__brand">Songhive</h1>
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
  text-align: center;
  margin: 0 0 var(--space-6);
}
</style>
