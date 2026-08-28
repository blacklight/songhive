<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { listHashtags, type HashtagSummary } from "@/api/hashtags";
import { useDebounce } from "@/composables/useDebounce";
import HashtagInput from "./HashtagInput.vue";

export interface Props {
  modelValue: string[];
  placeholder?: string;
  inputId?: string;
  ariaLabel?: string;
  debounce?: number;
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: undefined,
  inputId: undefined,
  ariaLabel: undefined,
  debounce: 200,
});

const emit = defineEmits<{
  "update:modelValue": [value: string[]];
  add: [hashtag: string];
  remove: [hashtag: string];
  select: [hashtag: string];
}>();

const { t } = useI18n();

const suggestions = ref<HashtagSummary[]>([]);
const loading = ref(false);
const open = ref(false);
const activeIndex = ref(-1);
const inputRef = ref<InstanceType<typeof HashtagInput> | null>(null);

const selectedSet = computed(() => new Set(props.modelValue));

const visibleSuggestions = computed(() =>
  suggestions.value.filter((h) => !selectedSet.value.has(h.name)),
);

async function fetchSuggestions(q: string) {
  if (!q.trim()) {
    suggestions.value = [];
    open.value = false;
    activeIndex.value = -1;
    return;
  }

  loading.value = true;
  try {
    const result = await listHashtags({ q, limit: 10 });
    suggestions.value = result.items;
    open.value = visibleSuggestions.value.length > 0;
    activeIndex.value = open.value ? 0 : -1;
  } catch {
    suggestions.value = [];
    open.value = false;
    activeIndex.value = -1;
  } finally {
    loading.value = false;
  }
}

const debouncedFetch = useDebounce(fetchSuggestions, props.debounce);

function onSearch(q: string) {
  debouncedFetch(q);
}

function onAdd(hashtag: string) {
  emit("add", hashtag);
  suggestions.value = [];
  open.value = false;
  activeIndex.value = -1;
}

function onRemove(hashtag: string) {
  emit("remove", hashtag);
}

function selectSuggestion(name: string) {
  inputRef.value?.addTag(name);
  emit("select", name);
  suggestions.value = [];
  open.value = false;
  activeIndex.value = -1;
}

function onKeyDown(event: KeyboardEvent) {
  if (!open.value) return;

  if (event.key === "ArrowDown") {
    event.preventDefault();
    activeIndex.value =
      (activeIndex.value + 1) % visibleSuggestions.value.length;
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    activeIndex.value =
      (activeIndex.value - 1 + visibleSuggestions.value.length) %
      visibleSuggestions.value.length;
  } else if (event.key === "Enter" && activeIndex.value >= 0) {
    event.preventDefault();
    const selected = visibleSuggestions.value[activeIndex.value];
    if (selected) {
      selectSuggestion(selected.name);
    }
  } else if (event.key === "Escape") {
    open.value = false;
    activeIndex.value = -1;
  }
}

function onFocus() {
  if (inputRef.value?.inputValue?.trim()) {
    debouncedFetch(inputRef.value.inputValue);
  }
}

function onBlur() {
  // Delay closing so click events on suggestions can fire.
  setTimeout(() => {
    open.value = false;
    activeIndex.value = -1;
  }, 150);
}

watch(
  () => props.modelValue,
  () => {
    // Re-filter suggestions as the selected set changes.
    if (open.value && visibleSuggestions.value.length === 0) {
      open.value = false;
      activeIndex.value = -1;
    }
  },
  { deep: true },
);
</script>

<template>
  <div class="hashtag-autocomplete" @keydown="onKeyDown">
    <HashtagInput
      ref="inputRef"
      :model-value="modelValue"
      :placeholder="placeholder"
      :input-id="inputId"
      :aria-label="ariaLabel"
      @update:model-value="$emit('update:modelValue', $event)"
      @add="onAdd"
      @remove="onRemove"
      @search="onSearch"
      @focus="onFocus"
      @blur="onBlur"
    />

    <ul
      v-if="open && visibleSuggestions.length"
      class="hashtag-autocomplete__suggestions"
      role="listbox"
    >
      <li
        v-for="(item, index) in visibleSuggestions"
        :key="item.name"
        class="hashtag-autocomplete__suggestion"
        :class="{
          'hashtag-autocomplete__suggestion--active': index === activeIndex,
        }"
        role="option"
        :aria-selected="index === activeIndex"
        @mousedown.prevent="selectSuggestion(item.name)"
      >
        <span class="hashtag-autocomplete__name">{{ item.name }}</span>
        <span class="hashtag-autocomplete__count">
          {{ t("hashtags.itemCount", { count: item.item_count }) }}
        </span>
      </li>
    </ul>

    <div
      v-if="loading && open"
      class="hashtag-autocomplete__loading"
      aria-live="polite"
    >
      {{ t("common.loading") }}
    </div>
  </div>
</template>

<style scoped>
.hashtag-autocomplete {
  position: relative;
}

.hashtag-autocomplete__suggestions {
  position: absolute;
  top: calc(100% + var(--space-1));
  left: 0;
  right: 0;
  z-index: 10;
  margin: 0;
  padding: var(--space-1) 0;
  list-style: none;
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  max-height: 16rem;
  overflow-y: auto;
}

.hashtag-autocomplete__suggestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  color: var(--color-text);
}

.hashtag-autocomplete__suggestion:hover,
.hashtag-autocomplete__suggestion--active {
  background-color: var(--color-surface-hover);
}

.hashtag-autocomplete__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hashtag-autocomplete__count {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.hashtag-autocomplete__loading {
  margin-top: var(--space-1);
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
</style>
