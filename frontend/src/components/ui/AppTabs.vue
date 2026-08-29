<script setup lang="ts">
export interface Tab {
  value: string;
  label: string;
}

export interface Props {
  modelValue: string;
  tabs: Tab[];
}

const props = defineProps<Props>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

function select(value: string) {
  if (value !== props.modelValue) {
    emit("update:modelValue", value);
  }
}
</script>

<template>
  <div class="app-tabs" role="tablist">
    <button
      v-for="tab in tabs"
      :key="tab.value"
      type="button"
      role="tab"
      :class="[
        'app-tabs__tab',
        { 'app-tabs__tab--active': tab.value === props.modelValue },
      ]"
      :aria-selected="tab.value === props.modelValue ? 'true' : 'false'"
      @click="select(tab.value)"
    >
      {{ tab.label }}
    </button>
  </div>
</template>

<style scoped>
.app-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.app-tabs__tab {
  padding: var(--space-2) var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background-color: transparent;
  color: var(--color-text);
  cursor: pointer;
  font-weight: 500;
  transition: background-color var(--transition-fast);
}

.app-tabs__tab:hover {
  background-color: var(--color-surface-hover);
}

.app-tabs__tab--active {
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
}
</style>
