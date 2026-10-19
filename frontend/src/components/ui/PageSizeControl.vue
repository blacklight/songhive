<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AppSelect from "./AppSelect.vue";

const CUSTOM_VALUE = "custom";

export interface Props {
  modelValue: number;
  options?: number[];
  min?: number;
  max?: number;
  label?: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  options: () => [10, 20, 50, 100],
  min: 1,
  max: 100,
  label: undefined,
});

const emit = defineEmits<{ "update:modelValue": [value: number] }>();

const { t } = useI18n();

// "Custom" is a UI mode, not a value: the select stays on the custom option
// while the user types, and also whenever the model carries a size that is
// not one of the presets.
const customMode = ref(!props.options.includes(props.modelValue));
const customValue = ref(String(props.modelValue));

const selectValue = computed(() =>
  customMode.value ? CUSTOM_VALUE : String(props.modelValue),
);

const selectOptions = computed(() => [
  ...props.options.map((n) => ({ value: String(n), label: String(n) })),
  { value: CUSTOM_VALUE, label: t("browse.list.customPageSize") },
]);

function onSelect(value: string) {
  if (value === CUSTOM_VALUE) {
    customMode.value = true;
    customValue.value = String(props.modelValue);
    return;
  }
  customMode.value = false;
  const n = Number(value);
  if (n !== props.modelValue) emit("update:modelValue", n);
}

function applyCustom() {
  const n = Number(String(customValue.value).trim());
  if (Number.isInteger(n) && n > 0) {
    const clamped = Math.min(props.max, Math.max(props.min, n));
    customValue.value = String(clamped);
    if (clamped !== props.modelValue) emit("update:modelValue", clamped);
  } else {
    customValue.value = String(props.modelValue);
  }
}

watch(
  () => props.modelValue,
  (value) => {
    if (props.options.includes(value)) {
      customMode.value = false;
    } else {
      customMode.value = true;
      customValue.value = String(value);
    }
  },
);
</script>

<template>
  <div class="page-size-control">
    <AppSelect
      :model-value="selectValue"
      :options="selectOptions"
      :label="props.label ?? t('browse.list.perPage')"
      :disabled="props.disabled"
      @update:model-value="onSelect"
    />
    <input
      v-if="customMode"
      v-model="customValue"
      type="number"
      :min="props.min"
      :max="props.max"
      class="page-size-control__input"
      :aria-label="t('browse.list.customPageSizeInput')"
      :placeholder="t('browse.list.customPageSizeInput')"
      :disabled="props.disabled"
      @keyup.enter="applyCustom"
      @blur="applyCustom"
    />
  </div>
</template>

<style scoped>
.page-size-control {
  display: inline-flex;
  align-items: flex-end;
  gap: var(--space-2);
}

.page-size-control__input {
  width: 6rem;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
}

.page-size-control__input:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.page-size-control__input:disabled {
  opacity: 0.5;
}
</style>
