<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useDebounce } from "@/composables/useDebounce";

export interface Props {
  modelValue: string;
  placeholder?: string;
  debounce?: number;
}

const props = withDefaults(defineProps<Props>(), {
  debounce: 300,
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  search: [value: string];
}>();
const { t } = useI18n();

const localValue = ref(props.modelValue);

const debouncedEmit = useDebounce((value: string) => {
  emit("update:modelValue", value);
}, props.debounce);

watch(
  () => props.modelValue,
  (value) => {
    localValue.value = value;
  },
);

function onInput(event: Event) {
  const target = event.target as HTMLInputElement;
  localValue.value = target.value;
  debouncedEmit(localValue.value);
}

function onKeyDown(event: KeyboardEvent) {
  if (event.key === "Enter") {
    debouncedEmit.cancel();
    emit("update:modelValue", localValue.value);
    emit("search", localValue.value);
  }
}

function clear() {
  localValue.value = "";
  debouncedEmit.cancel();
  emit("update:modelValue", "");
}
</script>

<template>
  <div class="search-bar">
    <input
      :value="localValue"
      type="search"
      class="search-bar__input"
      :placeholder="props.placeholder"
      @input="onInput"
      @keydown="onKeyDown"
    />
    <button
      v-if="localValue"
      type="button"
      class="search-bar__clear"
      :aria-label="t('common.close')"
      @click="clear"
    >
      ×
    </button>
  </div>
</template>

<style scoped>
.search-bar {
  position: relative;
  display: flex;
  align-items: center;
}

.search-bar__input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  padding-right: 2rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
}

.search-bar__input:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.search-bar__clear {
  position: absolute;
  right: var(--space-2);
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-size: 1.25rem;
  line-height: 1;
}
</style>
