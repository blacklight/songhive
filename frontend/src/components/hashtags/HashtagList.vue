<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  hashtags: string[];
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
  click: [hashtag: string];
  remove: [hashtag: string];
}>();

const { t } = useI18n();

function onClick(hashtag: string) {
  if (props.clickable) {
    emit("click", hashtag);
  }
}

function onRemove(hashtag: string, event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
  emit("remove", hashtag);
}
</script>

<template>
  <ul class="hashtag-list">
    <li
      v-for="hashtag in hashtags"
      :key="hashtag"
      class="hashtag-list__item"
      :class="`hashtag-list__item--${size}`"
    >
      <component
        :is="clickable ? RouterLink : 'span'"
        :to="clickable ? `/hashtags/${encodeURIComponent(hashtag)}` : undefined"
        class="hashtag-list__chip"
        :class="{ 'hashtag-list__chip--clickable': clickable }"
        @click="clickable ? onClick(hashtag) : undefined"
      >
        <AppIcon name="hashtag" />
        <span class="hashtag-list__name">{{ hashtag }}</span>
        <button
          v-if="removable"
          type="button"
          class="hashtag-list__remove"
          :aria-label="t('hashtags.removeTag', { tag: hashtag })"
          @click="onRemove(hashtag, $event)"
        >
          <AppIcon name="xmark" />
        </button>
      </component>
    </li>
  </ul>
</template>

<style scoped>
.hashtag-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}

.hashtag-list__item {
  display: inline-flex;
}

.hashtag-list__chip {
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

.hashtag-list__chip--clickable:hover {
  background-color: var(--color-surface-hover);
  text-decoration: none;
}

.hashtag-list__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hashtag-list__remove {
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
