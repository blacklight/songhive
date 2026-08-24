<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useToastStore, type Toast } from "@/stores/toast";

const store = useToastStore();
const { t } = useI18n();

function toastRole(toast: Toast): "status" | "alert" {
  return toast.type === "error" || toast.type === "warning"
    ? "alert"
    : "status";
}
</script>

<template>
  <div class="app-toast" :style="{ 'z-index': 'var(--z-toast)' }">
    <transition-group name="toast" tag="ul" class="app-toast__list">
      <li
        v-for="toast in store.toasts"
        :key="toast.id"
        :class="['app-toast__item', `app-toast__item--${toast.type}`]"
        :role="toastRole(toast)"
        aria-live="polite"
      >
        <span class="app-toast__message">{{ toast.message }}</span>
        <button
          type="button"
          class="app-toast__close"
          :aria-label="t('common.close')"
          @click="store.dismiss(toast.id)"
        >
          ×
        </button>
      </li>
    </transition-group>
  </div>
</template>

<style scoped>
.app-toast {
  position: fixed;
  top: var(--space-4);
  right: var(--space-4);
}

.app-toast__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.app-toast__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background-color: var(--color-surface-raised);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  color: var(--color-text);
}

.app-toast__item--error {
  border-left: 4px solid var(--color-danger);
}

.app-toast__item--warning {
  border-left: 4px solid var(--color-warning);
}

.app-toast__item--success {
  border-left: 4px solid var(--color-success);
}

.app-toast__item--info {
  border-left: 4px solid var(--color-info);
}

.app-toast__close {
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-size: 1.25rem;
  line-height: 1;
  margin-left: auto;
}

.toast-enter-active,
.toast-leave-active {
  transition: all var(--transition-base);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(1rem);
}

@media (prefers-reduced-motion: reduce) {
  .toast-enter-active,
  .toast-leave-active {
    transition: none;
  }
}
</style>
