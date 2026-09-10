<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import { useDebounce } from "@/composables/useDebounce";
import { useOnClickOutside } from "@/composables/useOnClickOutside";
import type {
  SearchEntity,
  SearchResultItem,
  SearchResultSection,
} from "@/api/search";

import AppIcon from "./AppIcon.vue";
import SearchSuggestions from "./SearchSuggestions.vue";

export interface Props {
  modelValue: string;
  placeholder?: string;
  debounce?: number;
  autocomplete?: boolean;
  autocompleteEntities?: SearchEntity[];
  autocompleteMinLength?: number;
  autocompleteLimit?: number;
  autocompleteDelay?: number;
  autocompleteFetcher?: (
    query: string,
    entities: SearchEntity[],
    limit: number,
  ) => Promise<SearchResultSection[]>;
}

const props = withDefaults(defineProps<Props>(), {
  debounce: 300,
  autocomplete: false,
  autocompleteEntities: () => [
    "tracks",
    "albums",
    "artists",
    "playlists",
    "libraries",
    "users",
    "tags",
    "genres",
  ],
  autocompleteMinLength: 2,
  autocompleteLimit: 5,
  autocompleteDelay: 750,
});

const emit = defineEmits<{
  "update:modelValue": [value: string];
  search: [value: string];
  "select-suggestion": [item: SearchResultItem];
  "autocomplete-error": [error: unknown];
}>();
const { t } = useI18n();

const localValue = ref(props.modelValue);
const rootEl = ref<HTMLElement | null>(null);
const suggestions = ref<SearchResultSection[]>([]);
const suggestionsOpen = ref(false);
const suggestionsLoading = ref(false);
const suggestionsError = ref<string | null>(null);
let requestSeq = 0;

const debouncedEmit = useDebounce((value: string) => {
  emit("update:modelValue", value);
}, props.debounce);

const debouncedFetch = useDebounce(async (value: string) => {
  if (!props.autocomplete || !props.autocompleteFetcher) {
    closeSuggestions();
    return;
  }
  const term = value.trim();
  if (term.length < props.autocompleteMinLength) {
    closeSuggestions();
    return;
  }
  const seq = ++requestSeq;
  suggestionsLoading.value = true;
  suggestionsError.value = null;
  try {
    const result = await props.autocompleteFetcher(
      term,
      props.autocompleteEntities,
      props.autocompleteLimit,
    );
    if (seq !== requestSeq) {
      return;
    }
    suggestions.value = result;
    suggestionsOpen.value = true;
  } catch (err) {
    if (seq !== requestSeq) {
      return;
    }
    emit("autocomplete-error", err);
    suggestionsError.value = err instanceof Error ? err.message : String(err);
    suggestionsOpen.value = true;
  } finally {
    if (seq === requestSeq) {
      suggestionsLoading.value = false;
    }
  }
}, props.autocompleteDelay);

watch(
  () => props.modelValue,
  (value) => {
    localValue.value = value;
    if (!value) {
      closeSuggestions();
    }
  },
);

function closeSuggestions() {
  suggestionsOpen.value = false;
  suggestionsLoading.value = false;
  suggestionsError.value = null;
  suggestions.value = [];
}

function onInput(event: Event) {
  const target = event.target as HTMLInputElement;
  localValue.value = target.value;
  debouncedEmit(localValue.value);
  if (props.autocomplete) {
    debouncedFetch(localValue.value);
  }
}

function onKeyDown(event: KeyboardEvent) {
  if (event.key === "Enter") {
    debouncedEmit.cancel();
    debouncedFetch.cancel();
    closeSuggestions();
    emit("update:modelValue", localValue.value);
    emit("search", localValue.value);
  } else if (event.key === "Escape") {
    closeSuggestions();
  }
}

function clear() {
  localValue.value = "";
  debouncedEmit.cancel();
  debouncedFetch.cancel();
  closeSuggestions();
  emit("update:modelValue", "");
}

function onSelect(item: SearchResultItem) {
  emit("select-suggestion", item);
  closeSuggestions();
}

useOnClickOutside(() => rootEl.value, closeSuggestions);
</script>

<template>
  <div ref="rootEl" class="search-bar">
    <AppIcon
      name="magnifying-glass"
      class="search-bar__search-icon"
      aria-hidden="true"
    />
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
      <AppIcon name="xmark" />
    </button>
    <SearchSuggestions
      v-if="suggestionsOpen"
      :sections="suggestions"
      :loading="suggestionsLoading"
      :error="suggestionsError"
      @select="onSelect"
    />
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
  padding: var(--space-2) 2rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
}

.search-bar__search-icon {
  position: absolute;
  left: var(--space-3);
  color: var(--color-text-muted);
  pointer-events: none;
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
