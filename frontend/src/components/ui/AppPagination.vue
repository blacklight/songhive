<script setup lang="ts">
import { computed } from "vue";
import AppButton from "./AppButton.vue";

export interface Props {
  page: number;
  total: number;
  perPage: number;
}

const props = defineProps<Props>();
const emit = defineEmits<{ "update:page": [page: number] }>();

const totalPages = computed(() =>
  Math.max(1, Math.ceil(props.total / props.perPage)),
);
const canPrev = computed(() => props.page > 1);
const canNext = computed(() => props.page < totalPages.value);

function prev() {
  if (canPrev.value) emit("update:page", props.page - 1);
}

function next() {
  if (canNext.value) emit("update:page", props.page + 1);
}
</script>

<template>
  <nav class="app-pagination" aria-label="Pagination">
    <AppButton
      size="sm"
      :disabled="!canPrev"
      :aria-label="`Page ${page - 1}`"
      :title="`Page ${page - 1}`"
      icon="chevron-left"
      @click="prev"
    />
    <span class="app-pagination__info">{{ page }} / {{ totalPages }}</span>
    <AppButton
      size="sm"
      :disabled="!canNext"
      :aria-label="`Page ${page + 1}`"
      :title="`Page ${page + 1}`"
      icon="chevron-right"
      @click="next"
    />
  </nav>
</template>

<style scoped>
.app-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
}

.app-pagination__info {
  color: var(--color-text);
  font-size: 0.875rem;
}
</style>
