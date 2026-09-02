<script setup lang="ts">
import { computed } from "vue";

export interface Option {
  value: string;
  label: string;
}

export interface Props {
  modelValue: string;
  options: Option[];
  label?: string;
  hint?: string;
  error?: string;
  disabled?: boolean;
}

const props = defineProps<Props>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const selectId = computed(
  () => `app-select-${Math.random().toString(36).slice(2)}`,
);

function onChange(event: Event) {
  const target = event.target as HTMLSelectElement;
  emit("update:modelValue", target.value);
}
</script>

<template>
  <div class="app-select">
    <label v-if="props.label" :for="selectId" class="app-select__label">
      {{ props.label }}
    </label>
    <select
      :id="selectId"
      :value="props.modelValue"
      :disabled="props.disabled"
      :aria-invalid="!!props.error"
      class="app-select__field"
      @change="onChange"
    >
      <option
        v-for="option in props.options"
        :key="option.value"
        :value="option.value"
      >
        {{ option.label }}
      </option>
    </select>
    <p v-if="props.hint" class="app-select__hint">{{ props.hint }}</p>
    <p v-if="props.error" class="app-select__error" role="alert">
      {{ props.error }}
    </p>
  </div>
</template>

<style scoped>
.app-select {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.app-select__label {
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 500;
}

.app-select__field {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
}

.app-select__field:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.app-select__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.app-select__error {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-danger);
}
</style>
