<script setup lang="ts">
import { computed, ref, useTemplateRef } from "vue";
import { useI18n } from "vue-i18n";
import { INDEX_LETTERS } from "@/utils/indexLetters";

export interface Props {
  /** Letters that have at least one item; the rest render dimmed. */
  available: ReadonlySet<string>;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  jump: [letter: string];
}>();

const { t } = useI18n();

const stripRef = useTemplateRef<HTMLElement>("strip");
const scrubbing = ref(false);
const activeLetter = ref<string | null>(null);
const focusedLetter = ref<string | null>(null);
let lastEmitted: string | null = null;

// Roving tabindex: the whole strip is a single Tab stop.
const tabbableLetter = computed(() => {
  if (focusedLetter.value && props.available.has(focusedLetter.value)) {
    return focusedLetter.value;
  }
  return INDEX_LETTERS.find((l) => props.available.has(l)) ?? "A";
});

function letterAtY(clientY: number): string {
  const strip = stripRef.value;
  if (!strip) return INDEX_LETTERS[0];
  const rect = strip.getBoundingClientRect();
  const idx = Math.min(
    INDEX_LETTERS.length - 1,
    Math.max(
      0,
      Math.floor((clientY - rect.top) / (rect.height / INDEX_LETTERS.length)),
    ),
  );
  return INDEX_LETTERS[idx];
}

function jumpTo(letter: string) {
  activeLetter.value = letter;
  if (!props.available.has(letter) || letter === lastEmitted) return;
  lastEmitted = letter;
  emit("jump", letter);
}

function onPointerDown(event: PointerEvent) {
  const strip = stripRef.value;
  if (!strip) return;
  try {
    strip.setPointerCapture?.(event.pointerId);
  } catch {
    // No active pointer for this id (e.g. synthesized events) — the
    // scrub still works for pointers that stay over the strip.
  }
  scrubbing.value = true;
  lastEmitted = null;
  jumpTo(letterAtY(event.clientY));
}

function onPointerMove(event: PointerEvent) {
  if (!scrubbing.value) return;
  jumpTo(letterAtY(event.clientY));
}

function onPointerUp() {
  scrubbing.value = false;
  activeLetter.value = null;
}

function onLetterClick(event: MouseEvent, letter: string) {
  // detail === 0 marks keyboard/assistive-tech activation; pointer
  // clicks were already handled by pointerdown.
  if (event.detail !== 0 || !props.available.has(letter)) return;
  lastEmitted = letter;
  emit("jump", letter);
}

function focusLetter(letter: string) {
  focusedLetter.value = letter;
  stripRef.value
    ?.querySelector<HTMLElement>(`[data-letter="${CSS.escape(letter)}"]`)
    ?.focus();
}

function stepAvailable(from: number, direction: 1 | -1) {
  for (
    let idx = from + direction;
    idx >= 0 && idx < INDEX_LETTERS.length;
    idx += direction
  ) {
    const letter = INDEX_LETTERS[idx];
    if (props.available.has(letter)) {
      focusLetter(letter);
      return;
    }
  }
}

function onLetterKeydown(event: KeyboardEvent) {
  const current = (event.target as HTMLElement | null)?.dataset.letter;
  if (!current) return;
  const idx = INDEX_LETTERS.indexOf(current);
  if (event.key === "ArrowDown") {
    event.preventDefault();
    stepAvailable(idx, 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    stepAvailable(idx, -1);
  } else if (event.key === "Home") {
    event.preventDefault();
    stepAvailable(-1, 1);
  } else if (event.key === "End") {
    event.preventDefault();
    stepAvailable(INDEX_LETTERS.length, -1);
  }
}
</script>

<template>
  <div class="alphabet-index">
    <nav
      ref="strip"
      class="alphabet-index__strip"
      :aria-label="t('browse.list.alphabetIndex')"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @lostpointercapture="onPointerUp"
    >
      <button
        v-for="letter in INDEX_LETTERS"
        :key="letter"
        type="button"
        class="alphabet-index__letter"
        :class="{ 'alphabet-index__letter--active': letter === activeLetter }"
        :data-letter="letter"
        :disabled="!available.has(letter)"
        :tabindex="letter === tabbableLetter ? 0 : -1"
        @click="onLetterClick($event, letter)"
        @keydown="onLetterKeydown"
        @focus="focusedLetter = letter"
      >
        {{ letter }}
      </button>
    </nav>
    <span v-if="activeLetter" class="alphabet-index__bubble" aria-hidden="true">
      {{ activeLetter }}
    </span>
  </div>
</template>

<style scoped>
.alphabet-index__strip {
  position: fixed;
  top: 50%;
  right: var(--space-1);
  transform: translateY(-50%);
  z-index: var(--z-player);
  display: flex;
  flex-direction: column;
  padding: 0;
  border-radius: var(--radius-full);
  background: color-mix(in srgb, var(--color-surface) 55%, transparent);
  user-select: none;
  touch-action: none;
}

.alphabet-index__letter {
  appearance: none;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-2);
  height: min(1.25rem, calc((100vh - 12rem) / 27));
  min-height: 0.45rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  font: inherit;
  font-size: 0.7rem;
  font-weight: 600;
  line-height: 1;
  color: var(--color-text-link);
  cursor: pointer;
}

.alphabet-index__letter:hover:not(:disabled),
.alphabet-index__letter--active:not(:disabled) {
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
}

.alphabet-index__letter:disabled {
  color: var(--color-text-muted);
  opacity: 0.45;
  cursor: default;
}

.alphabet-index__letter:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.alphabet-index__bubble {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: var(--z-toast);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 4.5rem;
  height: 4.5rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  box-shadow: var(--shadow-lg);
  font-size: 2rem;
  font-weight: 700;
  pointer-events: none;
}
</style>
