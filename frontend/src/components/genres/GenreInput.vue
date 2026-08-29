<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  modelValue: string[];
  placeholder?: string;
  id?: string;
  inputId?: string;
  ariaLabel?: string;
}

const props = withDefaults(defineProps<Props>(), {
  placeholder: undefined,
  id: undefined,
  inputId: undefined,
  ariaLabel: undefined,
});

const emit = defineEmits<{
  "update:modelValue": [value: string[]];
  add: [genre: string];
  remove: [genre: string];
  search: [value: string];
  focus: [];
  blur: [];
}>();

const { t } = useI18n();

const inputValue = ref("");
const error = ref<string | null>(null);
const inputRef = ref<HTMLInputElement | null>(null);

const containerId = computed(
  () => props.id || `genre-input-${Math.random().toString(36).slice(2)}`,
);
const inputId = computed(() => props.inputId || `${containerId.value}-input`);

const MAX_GENRE_LEN = 128;
const VALID_GENRE = /^(?=.*[a-z])[a-z0-9&/\-_ ]+$/;
const HAS_LETTER = /[a-z]/;
const SPLIT_DELIMS = /[,;]+/;

function normalize(raw: string): string {
  return raw.replace(/^#+/, "").toLowerCase().trim();
}

function validateTag(raw: string): string | null {
  const tag = normalize(raw);
  if (!tag) {
    return t("genres.emptyTag");
  }
  if (tag.length > MAX_GENRE_LEN) {
    return t("genres.tooLong");
  }
  if (!HAS_LETTER.test(tag)) {
    return t("genres.noLetter");
  }
  if (!VALID_GENRE.test(tag)) {
    return t("genres.invalidCharacters");
  }
  return null;
}

function addTag(raw: string) {
  const tag = normalize(raw);
  if (!tag) {
    error.value = null;
    inputValue.value = "";
    return;
  }

  const validationError = validateTag(raw);
  if (validationError) {
    error.value = validationError;
    return;
  }

  if (props.modelValue.includes(tag)) {
    inputValue.value = "";
    error.value = null;
    return;
  }

  const next = [...props.modelValue, tag];
  emit("update:modelValue", next);
  emit("add", tag);
  inputValue.value = "";
  error.value = null;
}

function removeTag(index: number) {
  const removed = props.modelValue[index];
  const next = [...props.modelValue];
  next.splice(index, 1);
  emit("update:modelValue", next);
  if (removed) {
    emit("remove", removed);
  }
}

function onInput(event: Event) {
  const target = event.target as HTMLInputElement;
  const value = target.value;

  if (value.includes(",") || value.includes(";") || value.includes("|")) {
    const parts = value.split(SPLIT_DELIMS).filter(Boolean);
    const lastPart = parts.length ? parts[parts.length - 1] : "";
    const endsWithDelim = /[,;|]$/.test(value);
    const complete = endsWithDelim ? parts : parts.slice(0, -1);
    for (const part of complete) {
      addTag(part);
    }
    inputValue.value = complete.length === parts.length ? "" : lastPart;
    error.value = null;
    emit("search", inputValue.value);
  } else {
    inputValue.value = value;
    if (error.value) {
      error.value = null;
    }
    emit("search", inputValue.value);
  }
}

function onKeyDown(event: KeyboardEvent) {
  if (event.key === "Enter") {
    event.preventDefault();
    addTag(inputValue.value);
    return;
  }

  if (event.key === "," || event.key === ";") {
    event.preventDefault();
    addTag(inputValue.value);
    return;
  }

  if (event.key === "Backspace" && !inputValue.value) {
    event.preventDefault();
    if (props.modelValue.length > 0) {
      removeTag(props.modelValue.length - 1);
    }
  }
}

function onBlur() {
  emit("blur");
  if (inputValue.value.trim()) {
    addTag(inputValue.value);
  }
}

function onFocus() {
  emit("focus");
  emit("search", inputValue.value);
}

function onPaste(event: ClipboardEvent) {
  const text = event.clipboardData?.getData("text");
  if (!text) return;

  const parts = text.split(SPLIT_DELIMS).filter(Boolean);
  if (parts.length > 1) {
    event.preventDefault();
    for (const part of parts) {
      addTag(part);
    }
  }
}

function focusInput() {
  inputRef.value?.focus();
}

function clearInput() {
  inputValue.value = "";
  error.value = null;
}

defineExpose({
  inputValue,
  focus: focusInput,
  addTag,
  removeTag,
  clearInput,
});
</script>

<template>
  <div class="genre-input">
    <div
      :id="containerId"
      class="genre-input__container"
      :class="{ 'genre-input__container--error': !!error }"
      @click="focusInput"
    >
      <span
        v-for="(tag, index) in modelValue"
        :key="tag"
        class="genre-input__chip"
      >
        <span class="genre-input__chip-text">{{ tag }}</span>
        <button
          type="button"
          class="genre-input__chip-remove"
          :aria-label="t('genres.removeTag', { tag })"
          @click.stop="removeTag(index)"
        >
          <AppIcon name="xmark" />
        </button>
      </span>
      <input
        :id="inputId"
        ref="inputRef"
        v-model="inputValue"
        type="text"
        class="genre-input__field"
        :placeholder="placeholder || t('genres.placeholder')"
        :aria-label="ariaLabel || t('genres.ariaLabel')"
        @input="onInput"
        @keydown="onKeyDown"
        @focus="onFocus"
        @blur="onBlur"
        @paste="onPaste"
      />
    </div>
    <p v-if="error" class="genre-input__error" role="alert">{{ error }}</p>
    <p class="genre-input__hint">{{ t("genres.hint") }}</p>
  </div>
</template>

<style scoped>
.genre-input {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.genre-input__container {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  min-height: 2.75rem;
  cursor: text;
}

.genre-input__container:focus-within {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.genre-input__container--error {
  border-color: var(--color-danger);
}

.genre-input__chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
  color: var(--color-accent-contrast);
  font-size: 0.875rem;
  max-width: 100%;
}

.genre-input__chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.genre-input__chip-remove {
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

.genre-input__field {
  flex: 1;
  min-width: 6rem;
  border: none;
  background: transparent;
  color: var(--color-text);
  font-size: 1rem;
  padding: var(--space-1) 0;
  outline: none;
}

.genre-input__error {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-danger);
}

.genre-input__hint {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
</style>
