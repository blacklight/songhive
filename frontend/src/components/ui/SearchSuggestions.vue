<script setup lang="ts">
import { useI18n } from "vue-i18n";

import type { SearchResultItem, SearchResultSection } from "@/api/search";

export interface Props {
  sections: SearchResultSection[];
  loading: boolean;
  error: string | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  select: [item: SearchResultItem];
}>();
const { t } = useI18n();
</script>

<template>
  <div class="search-suggestions" role="listbox">
    <div v-if="props.loading" class="search-suggestions__loading">
      {{ t("search.loading") }}
    </div>
    <div v-else-if="props.error" class="search-suggestions__error" role="alert">
      {{ t("search.error") }}
    </div>
    <div
      v-else-if="props.sections.length === 0"
      class="search-suggestions__empty"
    >
      {{ t("search.noResults") }}
    </div>
    <template v-else>
      <div
        v-for="section in props.sections"
        :key="section.entity"
        class="search-suggestions__section"
      >
        <div class="search-suggestions__section-header">
          {{ t(`search.entities.${section.entity}`) }}
          <span class="search-suggestions__count">({{ section.total }})</span>
        </div>
        <button
          v-for="item in section.items"
          :key="`${section.entity}-${item.id ?? item.name ?? item.title}`"
          type="button"
          class="search-suggestions__item"
          role="option"
          @click="emit('select', item)"
        >
          <span v-if="item.image_url" class="search-suggestions__thumb">
            <img :src="item.image_url" alt="" loading="lazy" />
          </span>
          <span
            v-else
            class="search-suggestions__thumb search-suggestions__thumb--empty"
          />
          <span class="search-suggestions__text">
            <span class="search-suggestions__title">{{ item.title }}</span>
            <span v-if="item.subtitle" class="search-suggestions__subtitle">
              {{ item.subtitle }}
            </span>
          </span>
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.search-suggestions {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 20;
  margin-top: var(--space-1);
  max-height: 24rem;
  overflow-y: auto;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg, 0 8px 24px rgb(0 0 0 / 0.12));
}

.search-suggestions__loading,
.search-suggestions__empty,
.search-suggestions__error {
  padding: var(--space-3);
  color: var(--color-text-muted);
}

.search-suggestions__error {
  color: var(--color-error);
}

.search-suggestions__section + .search-suggestions__section {
  border-top: 1px solid var(--color-border);
}

.search-suggestions__section-header {
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

.search-suggestions__count {
  font-weight: normal;
}

.search-suggestions__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: none;
  border: none;
  cursor: pointer;
  color: var(--color-text);
  text-align: left;
}

.search-suggestions__item:hover,
.search-suggestions__item:focus-visible {
  background: var(--color-surface-hover);
  outline: none;
}

.search-suggestions__thumb {
  width: 2.5rem;
  height: 2.5rem;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  background: var(--color-surface-raised);
  overflow: hidden;
}

.search-suggestions__thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.search-suggestions__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.search-suggestions__title {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.search-suggestions__subtitle {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
