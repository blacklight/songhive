<script setup lang="ts">
import {
  nextTick,
  useTemplateRef,
  watch,
  type ComponentPublicInstance,
} from "vue";
import { useI18n } from "vue-i18n";
import { usePlayerStore } from "@/stores/player";
import { formatTime } from "@/utils/time";
import AppButton from "@/components/ui/AppButton.vue";
import { useFocusTrap } from "@/composables/useFocusTrap";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();

type FocusableTarget = HTMLElement | ComponentPublicInstance | null | undefined;

export interface Props {
  open: boolean;
  returnFocusTo?: FocusableTarget;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: "close"): void;
}>();

const store = usePlayerStore();
const panelRef = useTemplateRef<HTMLElement>("panel");

function isOpen() {
  return props.open;
}

function getPanelRef() {
  return panelRef.value;
}

useFocusTrap(isOpen, getPanelRef);

watch(
  () => props.open,
  (open) => {
    if (open) {
      nextTick(() => {
        const first = panelRef.value?.querySelector<HTMLElement>(
          'button, [href], input, [tabindex]:not([tabindex="-1"])',
        );
        first?.focus();
      });
    } else if (props.returnFocusTo) {
      nextTick(() => {
        const target = props.returnFocusTo;
        if (!target) return;
        if (target instanceof HTMLElement) {
          target.focus();
        } else if ("$el" in target && target.$el instanceof HTMLElement) {
          target.$el.focus();
        }
      });
    }
  },
);

function onKeyDown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
  }
}

function playAtIndex(index: number) {
  store.playAll(store.queue, index);
}

function removeAt(event: MouseEvent, index: number) {
  event.stopPropagation();
  store.removeAt(index);
}

function clearQueue() {
  store.clear();
  emit("close");
}
</script>

<template>
  <div
    v-if="open"
    ref="panel"
    class="queue-panel"
    role="dialog"
    :aria-label="t('player.queue')"
    aria-modal="true"
    @keydown="onKeyDown"
  >
    <div class="queue-panel__header">
      <AppPageTitle
        :level="2"
        class="queue-panel__title"
        icon="list"
        icon-variant="solid"
      >
        {{ t("player.queue") }}
      </AppPageTitle>
      <AppButton
        variant="ghost"
        size="sm"
        class="queue-panel__clear"
        :aria-label="t('player.clearQueue')"
        :title="t('player.clearQueue')"
        icon="xmark"
        @click="clearQueue"
      >
        {{ t("common.clear") }}
      </AppButton>
      <AppButton
        variant="ghost"
        size="sm"
        class="queue-panel__close"
        :aria-label="t('player.closeQueue')"
        :title="t('player.closeQueue')"
        icon="xmark"
        @click="emit('close')"
      />
    </div>

    <ol
      class="queue-panel__list"
      role="listbox"
      :aria-label="t('player.queueTracks')"
    >
      <!-- Drag-to-reorder is deferred to a later phase. -->
      <li
        v-for="(track, i) in store.queue"
        :key="track.id"
        class="queue-panel__item"
        :class="{ 'queue-panel__item--current': i === store.index }"
        role="option"
        :aria-selected="i === store.index"
        tabindex="0"
        @dblclick="playAtIndex(i)"
        @keydown.enter.prevent="playAtIndex(i)"
      >
        <span class="queue-panel__index" aria-hidden="true">{{ i + 1 }}</span>
        <img
          v-if="track.artwork_url"
          :src="track.artwork_url"
          alt=""
          class="queue-panel__artwork"
        />
        <div
          v-else
          class="queue-panel__artwork queue-panel__artwork--placeholder"
        />
        <div class="queue-panel__meta">
          <p class="queue-panel__track-title" :title="track.title">
            {{ track.title }}
          </p>
          <p class="queue-panel__track-artist" :title="track.artist_name">
            {{ track.artist_name }}
          </p>
        </div>
        <time class="queue-panel__duration" aria-hidden="true">
          {{ formatTime(track.duration ?? 0) }}
        </time>
        <AppButton
          variant="ghost"
          size="sm"
          class="queue-panel__remove"
          :aria-label="t('player.removeFromQueue', { title: track.title })"
          :title="t('player.removeFromQueue', { title: track.title })"
          icon="xmark"
          @click="removeAt($event, i)"
        />
      </li>
    </ol>

    <div v-if="store.queue.length === 0" class="queue-panel__empty">
      {{ t("player.emptyQueue") }}
    </div>
  </div>
</template>

<style scoped>
.queue-panel {
  position: fixed;
  bottom: var(--player-bar-height, 5rem);
  right: 0;
  width: min(24rem, calc(100vw - 1rem));
  max-height: min(60vh, 30rem);
  display: flex;
  flex-direction: column;
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  box-shadow: var(--shadow-lg);
  z-index: var(--z-player);
  overflow: hidden;
}

.queue-panel__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.queue-panel__title {
  flex: 1;
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
}

.queue-panel__list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
}

.queue-panel__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  transition: background-color var(--transition-fast);
  outline: none;
}

.queue-panel__item:hover,
.queue-panel__item:focus-visible {
  background-color: var(--color-surface-raised);
}

.queue-panel__item--current {
  background-color: var(--color-surface-raised);
  border-left: 3px solid var(--color-accent);
}

.queue-panel__index {
  width: 1.5rem;
  text-align: center;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.queue-panel__artwork {
  width: 2.5rem;
  height: 2.5rem;
  object-fit: cover;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  background-color: var(--color-surface-raised);
}

.queue-panel__artwork--placeholder {
  background-color: var(--color-border);
}

.queue-panel__meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.queue-panel__track-title,
.queue-panel__track-artist {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-panel__track-title {
  font-weight: 500;
  color: var(--color-text);
}

.queue-panel__track-artist {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.queue-panel__duration {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
  min-width: 2.5rem;
  text-align: right;
}

.queue-panel__remove {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.queue-panel .queue-panel__clear,
.queue-panel .queue-panel__close,
.queue-panel .queue-panel__remove {
  font-size: 1rem;
}

.queue-panel__remove:hover {
  color: var(--color-danger);
}

.queue-panel__empty {
  padding: var(--space-4);
  text-align: center;
  color: var(--color-text-muted);
}

@media (max-width: 767px) {
  .queue-panel {
    left: 0;
    width: auto;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  }
}
</style>
