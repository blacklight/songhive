<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useFocusTrap } from "@/composables/useFocusTrap";

export interface Props {
  open: boolean;
  title?: string;
  closable?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  closable: true,
});

const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();

const dialogRef = ref<HTMLElement | null>(null);
const titleId = computed(() =>
  props.title
    ? `modal-title-${Math.random().toString(36).slice(2)}`
    : undefined,
);

function close() {
  if (props.closable) emit("close");
}

function onBackdropClick(event: MouseEvent) {
  if (event.target === dialogRef.value) close();
}

function onEsc(event: KeyboardEvent) {
  if (event.key === "Escape") close();
}

useFocusTrap(
  () => props.open,
  () => dialogRef.value,
);
</script>

<template>
  <Teleport to="body">
    <div
      v-if="props.open"
      ref="dialogRef"
      class="app-modal__overlay"
      :style="{ 'z-index': 'var(--z-modal)' }"
      @click="onBackdropClick"
      @keydown="onEsc"
    >
      <div
        role="dialog"
        aria-modal="true"
        class="app-modal"
        :aria-labelledby="titleId"
        tabindex="-1"
      >
        <div class="app-modal__header">
          <h2 v-if="props.title" :id="titleId" class="app-modal__title">
            {{ props.title }}
          </h2>
          <button
            v-if="props.closable"
            type="button"
            class="app-modal__close"
            :aria-label="t('common.close')"
            @click="close"
          >
            ×
          </button>
        </div>
        <div class="app-modal__body">
          <slot />
        </div>
        <div v-if="$slots.actions" class="app-modal__actions">
          <slot name="actions" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.app-modal__overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: rgba(0, 0, 0, 0.5);
  padding: var(--space-4);
}

.app-modal {
  background-color: var(--color-surface);
  color: var(--color-text);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  max-width: 32rem;
  width: 100%;
  max-height: calc(100vh - var(--space-8));
  overflow-y: auto;
  outline: none;
}

.app-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--space-4) 0;
}

.app-modal__title {
  margin: 0;
  font-size: 1.25rem;
}

.app-modal__close {
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-size: 1.5rem;
  line-height: 1;
}

.app-modal__body {
  padding: var(--space-4);
}

.app-modal__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: 0 var(--space-4) var(--space-4);
}
</style>
