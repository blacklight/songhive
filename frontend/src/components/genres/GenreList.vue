<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  genres: string[];
  removable?: boolean;
  clickable?: boolean;
  size?: "sm" | "md";
}

const props = withDefaults(defineProps<Props>(), {
  removable: false,
  clickable: true,
  size: "md",
});

const emit = defineEmits<{
  click: [genre: string];
  remove: [genre: string];
}>();

const { t } = useI18n();

function onClick(genre: string) {
  if (props.clickable) {
    emit("click", genre);
  }
}

function onRemove(genre: string, event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
  emit("remove", genre);
}
</script>

<template>
  <ul class="genre-list">
    <li
      v-for="genre in genres"
      :key="genre"
      class="genre-list__item"
      :class="`genre-list__item--${size}`"
    >
      <component
        :is="clickable ? RouterLink : 'span'"
        :to="clickable ? `/genres/${encodeURIComponent(genre)}` : undefined"
        class="genre-list__chip"
        :class="{ 'genre-list__chip--clickable': clickable }"
        @click="clickable ? onClick(genre) : undefined"
      >
        <AppIcon name="tag" />
        <span class="genre-list__name">{{ genre }}</span>
        <button
          v-if="removable"
          type="button"
          class="genre-list__remove"
          :aria-label="t('genres.removeTag', { tag: genre })"
          @click="onRemove(genre, $event)"
        >
          <AppIcon name="xmark" />
        </button>
      </component>
    </li>
  </ul>
</template>

<style scoped>
.genre-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}

.genre-list__item {
  display: inline-flex;
}

.genre-list__chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
  color: var(--color-accent-contrast);
  text-decoration: none;
  font-size: 0.875rem;
  max-width: 100%;
  transition: background-color var(--transition-fast);
}

.genre-list__chip--clickable:hover {
  background-color: var(--color-surface-hover);
  text-decoration: none;
}

.genre-list__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genre-list__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: inherit;
  cursor: pointer;
  padding: 0;
  font-size: 0.75rem;
  line-height: 1;
}
</style>
