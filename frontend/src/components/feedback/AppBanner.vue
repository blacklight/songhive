<script setup lang="ts">
import { useI18n } from "vue-i18n";

export interface Props {
  type: "info" | "success" | "warning" | "error";
  title?: string;
  dismissible?: boolean;
}

const props = defineProps<Props>();
const emit = defineEmits<{ dismiss: [] }>();

const { t } = useI18n();

function dismiss() {
  emit("dismiss");
}
</script>

<template>
  <div :class="['app-banner', `app-banner--${props.type}`]" role="status">
    <div class="app-banner__content">
      <strong v-if="props.title" class="app-banner__title">{{
        props.title
      }}</strong>
      <div class="app-banner__body">
        <slot />
      </div>
    </div>
    <button
      v-if="props.dismissible"
      type="button"
      class="app-banner__close"
      :aria-label="t('common.close')"
      @click="dismiss"
    >
      ×
    </button>
  </div>
</template>

<style scoped>
.app-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

.app-banner--info {
  border-left: 4px solid var(--color-info);
}

.app-banner--success {
  border-left: 4px solid var(--color-success);
}

.app-banner--warning {
  border-left: 4px solid var(--color-warning);
}

.app-banner--error {
  border-left: 4px solid var(--color-danger);
}

.app-banner__content {
  flex: 1;
}

.app-banner__title {
  display: block;
  margin-bottom: var(--space-1);
}

.app-banner__close {
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-size: 1.25rem;
  line-height: 1;
}
</style>
