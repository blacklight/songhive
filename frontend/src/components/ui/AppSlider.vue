<script setup lang="ts">
import { computed } from "vue";

export interface Props {
  modelValue: number;
  min?: number;
  max?: number;
  step?: number;
  ariaLabel?: string;
  ariaValueText?: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  min: 0,
  max: 100,
  step: 1,
  ariaLabel: undefined,
  ariaValueText: undefined,
  disabled: false,
});

const emit = defineEmits<{
  (e: "update:modelValue", value: number): void;
  (e: "input", value: number): void;
  (e: "change", value: number): void;
}>();

const valueText = computed(() => props.ariaValueText ?? `${props.modelValue}`);

function onInput(event: Event) {
  const target = event.target as HTMLInputElement;
  const value = Number(target.value);
  emit("update:modelValue", value);
  emit("input", value);
}

function onChange(event: Event) {
  const target = event.target as HTMLInputElement;
  emit("change", Number(target.value));
}
</script>

<template>
  <input
    type="range"
    class="app-slider"
    :min="props.min"
    :max="props.max"
    :step="props.step"
    :value="props.modelValue"
    :aria-label="props.ariaLabel"
    :aria-valuetext="valueText"
    :disabled="props.disabled"
    @input="onInput"
    @change="onChange"
  />
</template>

<style scoped>
.app-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 0.25rem;
  background-color: var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.app-slider:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.app-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 1rem;
  height: 1rem;
  border-radius: var(--radius-full);
  background-color: var(--color-accent);
  border: 2px solid var(--color-surface);
  box-shadow: var(--shadow-sm);
  transition:
    transform var(--transition-fast),
    background-color var(--transition-fast);
}

.app-slider::-moz-range-thumb {
  width: 1rem;
  height: 1rem;
  border-radius: var(--radius-full);
  background-color: var(--color-accent);
  border: 2px solid var(--color-surface);
  box-shadow: var(--shadow-sm);
  transition:
    transform var(--transition-fast),
    background-color var(--transition-fast);
}

.app-slider::-webkit-slider-thumb:hover {
  transform: scale(1.15);
}

.app-slider::-moz-range-thumb:hover {
  transform: scale(1.15);
}

.app-slider:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .app-slider,
  .app-slider::-webkit-slider-thumb,
  .app-slider::-moz-range-thumb {
    transition: none;
  }
}
</style>
