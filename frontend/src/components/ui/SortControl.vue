<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import AppSelect from "./AppSelect.vue";
import AppButton from "./AppButton.vue";

export interface Option {
  value: string;
  label: string;
}

export interface Props {
  modelValue: string;
  direction: "asc" | "desc";
  options: Option[];
  label?: string;
}

const props = withDefaults(defineProps<Props>(), {
  label: undefined,
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  "update:direction": [value: "asc" | "desc"];
}>();

const { t } = useI18n();

const icon = computed(() =>
  props.direction === "asc" ? "arrow-up-short-wide" : "arrow-down-wide-short",
);

const nextDirection = computed<"asc" | "desc">(() =>
  props.direction === "asc" ? "desc" : "asc",
);

const toggleTitle = computed(() =>
  props.direction === "asc" ? t("sort.ascending") : t("sort.descending"),
);

function onFieldChange(value: string) {
  emit("update:modelValue", value);
}

function onToggleDirection() {
  emit("update:direction", nextDirection.value);
}
</script>

<template>
  <div class="sort-control">
    <AppSelect
      :model-value="props.modelValue"
      :options="props.options"
      :label="props.label ?? t('sort.label')"
      @update:model-value="onFieldChange"
    />
    <AppButton
      size="sm"
      variant="secondary"
      :icon="icon"
      :title="toggleTitle"
      :aria-label="toggleTitle"
      @click="onToggleDirection"
    />
  </div>
</template>

<style scoped>
.sort-control {
  display: inline-flex;
  align-items: flex-end;
  gap: var(--space-2);
}
</style>
