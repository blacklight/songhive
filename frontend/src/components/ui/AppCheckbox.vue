<script setup lang="ts">
import { computed } from "vue";

let checkboxCounter = 0;

export interface Props {
  modelValue: boolean;
  label?: string;
  disabled?: boolean;
  indeterminate?: boolean;
  id?: string;
}

const props = withDefaults(defineProps<Props>(), {
  label: undefined,
  disabled: false,
  indeterminate: false,
});

const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

const fallbackId = `app-checkbox-${++checkboxCounter}`;
const inputId = computed(() => props.id || fallbackId);

function onChange(event: Event) {
  const target = event.target as HTMLInputElement;
  emit("update:modelValue", target.checked);
}
</script>

<template>
  <div class="app-checkbox">
    <input
      :id="inputId"
      type="checkbox"
      class="app-checkbox__input"
      :checked="props.modelValue"
      :disabled="props.disabled"
      :indeterminate.prop="props.indeterminate"
      @change="onChange"
    />
    <label v-if="props.label" :for="inputId" class="app-checkbox__label">
      {{ props.label }}
    </label>
  </div>
</template>

<style scoped>
.app-checkbox {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.app-checkbox__input {
  width: 1.125rem;
  height: 1.125rem;
  accent-color: var(--color-accent);
  cursor: pointer;
}

.app-checkbox__input:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.app-checkbox__label {
  color: var(--color-text);
  font-size: 0.875rem;
  cursor: pointer;
}
</style>
