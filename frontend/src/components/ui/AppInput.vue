<script setup lang="ts">
import { computed } from "vue";

export interface Props {
  modelValue: string | number;
  label?: string;
  type?: "text" | "email" | "password" | "number" | "url" | "search";
  error?: string;
  hint?: string;
  required?: boolean;
  disabled?: boolean;
  id?: string;
}

const props = withDefaults(defineProps<Props>(), {
  type: "text",
});

const emit = defineEmits<{ "update:modelValue": [value: string | number] }>();

const inputId = computed(
  () => props.id || `app-input-${Math.random().toString(36).slice(2)}`,
);
const hintId = computed(() => `hint-${inputId.value}`);
const errorId = computed(() => `error-${inputId.value}`);
const describedBy = computed(() => {
  const ids: string[] = [];
  if (props.hint) ids.push(hintId.value);
  if (props.error) ids.push(errorId.value);
  return ids.length > 0 ? ids.join(" ") : undefined;
});

function onInput(event: Event) {
  const target = event.target as HTMLInputElement;
  emit("update:modelValue", target.value);
}
</script>

<template>
  <div class="app-input">
    <label v-if="props.label" :for="inputId" class="app-input__label">
      {{ props.label }}
    </label>
    <input
      :id="inputId"
      :type="props.type"
      :value="props.modelValue"
      :required="props.required"
      :disabled="props.disabled"
      :aria-describedby="describedBy"
      :aria-invalid="!!props.error"
      class="app-input__field"
      @input="onInput"
    />
    <p v-if="props.hint" :id="hintId" class="app-input__hint">
      {{ props.hint }}
    </p>
    <p v-if="props.error" :id="errorId" class="app-input__error" role="alert">
      {{ props.error }}
    </p>
  </div>
</template>

<style scoped>
.app-input {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.app-input__label {
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 500;
}

.app-input__field {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
}

.app-input__field:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.app-input__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.app-input__error {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-danger);
}
</style>
