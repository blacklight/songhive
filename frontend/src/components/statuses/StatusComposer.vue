<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { useI18n } from "vue-i18n";

import type { ActivityVisibility } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { uploadFile } from "@/api/files";
import {
  searchPreview,
  type SearchEntity,
  type SearchResultItem,
  type SearchResultSection,
} from "@/api/search";
import {
  createStatus,
  STATUS_CONTENT_TYPE_MARKDOWN,
  STATUS_CONTENT_TYPES,
  type StatusContentType,
} from "@/api/statuses";
import { useDebounce } from "@/composables/useDebounce";
import { useOnClickOutside } from "@/composables/useOnClickOutside";
import { useAuthStore } from "@/stores/auth";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import SearchSuggestions from "@/components/ui/SearchSuggestions.vue";

export interface StatusComposerPayload {
  status: string;
  content_type: StatusContentType;
  visibility: ActivityVisibility;
  language: string | null;
  media_ids: string[];
  track_ids: string[];
}

interface AttachedMedia {
  id: string;
  name: string;
  uploading: boolean;
  progress: number;
}

interface AttachedTrack {
  id: string;
  title: string;
  subtitle?: string | null;
  imageUrl?: string | null;
}

export interface InitialMedia {
  id: string;
  name: string;
}

export interface InitialTrack {
  id: string;
  title: string;
  subtitle?: string | null;
  imageUrl?: string | null;
}

export interface Props {
  /** Custom submission handler; defaults to ``POST /statuses/``. */
  submit?: (payload: StatusComposerPayload) => Promise<unknown>;
  submitLabel?: string;
  placeholder?: string;
  allowFileAttachments?: boolean;
  allowTrackAttachments?: boolean;
  /** Permit submitting with no text and no attachments (e.g. track publish). */
  allowEmpty?: boolean;
  autofocus?: boolean;
  /** Pre-filled state for editing an existing status. */
  initialStatus?: string;
  initialContentType?: string | null;
  initialVisibility?: ActivityVisibility;
  initialLanguage?: string | null;
  initialMedia?: InitialMedia[];
  initialTracks?: InitialTrack[];
}

const props = withDefaults(defineProps<Props>(), {
  allowFileAttachments: true,
  allowTrackAttachments: true,
  allowEmpty: false,
  autofocus: false,
});

const emit = defineEmits<{
  submitted: [result: unknown];
}>();

const { t } = useI18n();
const authStore = useAuthStore();

// Mirrors ``_MAX_STATUS_ATTACHMENTS`` in songhive/services/activities.py.
const MAX_ATTACHMENTS = 4;

const VISIBILITIES: ActivityVisibility[] = [
  "public",
  "followers",
  "mentioned",
  "local",
  "private",
];

const COMMON_LANGUAGES = [
  "en",
  "en-US",
  "en-GB",
  "fr",
  "de",
  "es",
  "it",
  "pt",
  "pt-BR",
  "nl",
  "ja",
  "zh-CN",
  "zh-TW",
  "ko",
  "ru",
  "pl",
  "sv",
  "fi",
  "nb",
  "da",
  "cs",
  "uk",
];

const initialContentType = (
  STATUS_CONTENT_TYPES.includes(props.initialContentType as StatusContentType)
    ? props.initialContentType
    : authStore.user?.status_content_type || STATUS_CONTENT_TYPE_MARKDOWN
) as StatusContentType;

const text = ref(props.initialStatus ?? "");
const contentType = ref<StatusContentType>(initialContentType);
const visibility = ref<ActivityVisibility>(props.initialVisibility ?? "public");
const language = ref(
  // An explicitly provided ``initialLanguage`` (including ``null`` for an
  // activity without one) wins; otherwise default to the browser locale.
  props.initialLanguage !== undefined
    ? (props.initialLanguage ?? "")
    : browserLocale(),
);
const media = ref<AttachedMedia[]>(
  (props.initialMedia ?? []).map((m) => ({
    id: m.id,
    name: m.name,
    uploading: false,
    progress: 100,
  })),
);
const tracks = ref<AttachedTrack[]>(
  (props.initialTracks ?? []).map((t) => ({
    id: t.id,
    title: t.title,
    subtitle: t.subtitle,
    imageUrl: t.imageUrl,
  })),
);
const submitting = ref(false);
const error = ref<string | null>(null);

const textareaEl = ref<HTMLTextAreaElement | null>(null);
const editorEl = ref<HTMLElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const trackQuery = ref("");
const trackSearchOpen = ref(false);

const mentionOpen = ref(false);
const mentionLoading = ref(false);
const mentionError = ref<string | null>(null);
const mentionSections = ref<SearchResultSection[]>([]);
// Start offset of the ``@token`` currently being completed.
const mentionStart = ref(0);
let mentionSeq = 0;

function browserLocale(): string {
  const tag = navigator.language || "";
  return tag || "en";
}

const languageOptions = computed(() => {
  const tags = new Set(COMMON_LANGUAGES);
  if (language.value) tags.add(language.value);
  return [...tags];
});

const contentTypeOptions = computed(() =>
  STATUS_CONTENT_TYPES.map((value) => ({
    value,
    label: t(`statusComposer.contentTypes.${value}`),
  })),
);

const visibilityOptions = computed(() =>
  VISIBILITIES.map((value) => ({
    value,
    label: t(`activities.visibility.${value}`),
  })),
);

const uploading = computed(() => media.value.some((m) => m.uploading));

const canSubmit = computed(
  () =>
    !submitting.value &&
    !uploading.value &&
    (props.allowEmpty ||
      !!text.value.trim() ||
      media.value.length > 0 ||
      tracks.value.length > 0),
);

const canAttachMedia = computed(
  () => props.allowFileAttachments && media.value.length < MAX_ATTACHMENTS,
);
const canAttachTrack = computed(
  () => props.allowTrackAttachments && tracks.value.length < MAX_ATTACHMENTS,
);

function closeMention() {
  mentionOpen.value = false;
  mentionLoading.value = false;
  mentionError.value = null;
  mentionSections.value = [];
}

const debouncedMentionFetch = useDebounce(async (query: string) => {
  const seq = ++mentionSeq;
  mentionLoading.value = true;
  mentionError.value = null;
  try {
    const response = await searchPreview(query, ["users"], 5);
    if (seq !== mentionSeq) return;
    mentionSections.value = response.sections;
    mentionOpen.value = true;
  } catch (err) {
    if (seq !== mentionSeq) return;
    mentionError.value = err instanceof Error ? err.message : String(err);
    mentionSections.value = [];
    mentionOpen.value = true;
  } finally {
    if (seq === mentionSeq) mentionLoading.value = false;
  }
}, 300);

/** Detect an ``@token`` immediately before the caret and autocomplete it. */
function detectMention() {
  const el = textareaEl.value;
  if (!el) {
    closeMention();
    return;
  }
  const caret = el.selectionStart ?? text.value.length;
  const before = text.value.slice(0, caret);
  const match = before.match(/(^|\s)@([A-Za-z0-9_.-]*)$/);
  if (!match) {
    mentionSeq++;
    debouncedMentionFetch.cancel();
    closeMention();
    return;
  }
  mentionStart.value = caret - match[2].length - 1;
  const query = match[2];
  if (query.length === 0) {
    debouncedMentionFetch.cancel();
    closeMention();
    return;
  }
  debouncedMentionFetch(query);
}

function onTextInput() {
  detectMention();
}

function onEditorKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    mentionSeq++;
    debouncedMentionFetch.cancel();
    closeMention();
  }
}

async function onMentionSelect(item: SearchResultItem) {
  const username = item.name ?? item.id ?? item.title;
  const el = textareaEl.value;
  const caret = el?.selectionStart ?? text.value.length;
  const insertion = `@${username} `;
  text.value =
    text.value.slice(0, mentionStart.value) +
    insertion +
    text.value.slice(caret);
  mentionSeq++;
  debouncedMentionFetch.cancel();
  closeMention();
  await nextTick();
  if (el) {
    const pos = mentionStart.value + insertion.length;
    el.focus();
    el.setSelectionRange(pos, pos);
  }
}

async function trackAutocompleteFetcher(
  query: string,
  entities: SearchEntity[],
  limit: number,
) {
  const response = await searchPreview(query, entities, limit);
  return response.sections;
}

function onTrackSelect(item: SearchResultItem) {
  if (!item.id || tracks.value.some((track) => track.id === item.id)) {
    trackQuery.value = "";
    return;
  }
  tracks.value.push({
    id: item.id,
    title: item.title,
    subtitle: item.subtitle,
    imageUrl: item.image_url,
  });
  trackQuery.value = "";
  trackSearchOpen.value = false;
}

function removeTrack(id: string) {
  tracks.value = tracks.value.filter((track) => track.id !== id);
}

function triggerFileInput() {
  fileInput.value?.click();
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || !canAttachMedia.value) return;

  media.value.push({
    id: "",
    name: file.name,
    uploading: true,
    progress: 0,
  });
  // Mutate through the reactive array element — writes on the raw object
  // pushed above would not invalidate the ``uploading`` computed.
  const entry = media.value[media.value.length - 1];
  error.value = null;
  try {
    // Uploaded as private; ``create_status`` escalates the file's visibility
    // to whatever the posted status's audience requires.
    const uploaded = await uploadFile(
      file,
      "private",
      (percent) => {
        entry.progress = percent;
      },
      undefined,
      undefined,
      undefined,
      false,
    );
    entry.id = uploaded.id;
  } catch (err) {
    media.value = media.value.filter((m) => m !== entry);
    error.value = t("statusComposer.uploadError", {
      message: getApiErrorMessage(err) || t("errors.unknown"),
    });
  } finally {
    entry.uploading = false;
  }
}

function removeMedia(entry: AttachedMedia) {
  media.value = media.value.filter((m) => m !== entry);
}

function buildPayload(): StatusComposerPayload {
  return {
    status: text.value.trim(),
    content_type: contentType.value,
    visibility: visibility.value,
    language: language.value.trim() || null,
    media_ids: media.value.filter((m) => m.id).map((m) => m.id),
    track_ids: tracks.value.map((track) => track.id),
  };
}

async function onSubmit() {
  if (!canSubmit.value) return;
  submitting.value = true;
  error.value = null;
  try {
    const payload = buildPayload();
    const result = props.submit
      ? await props.submit(payload)
      : await createStatus(payload);
    text.value = "";
    media.value = [];
    tracks.value = [];
    trackSearchOpen.value = false;
    closeMention();
    emit("submitted", result);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    submitting.value = false;
  }
}

useOnClickOutside(() => editorEl.value, closeMention);
</script>

<template>
  <form class="status-composer" @submit.prevent="onSubmit">
    <div ref="editorEl" class="status-composer__editor">
      <textarea
        ref="textareaEl"
        v-model="text"
        class="status-composer__textarea"
        rows="4"
        :placeholder="props.placeholder ?? t('statusComposer.placeholder')"
        :disabled="submitting"
        :autofocus="props.autofocus"
        @input="onTextInput"
        @keydown="onEditorKeydown"
      />
      <SearchSuggestions
        v-if="mentionOpen"
        :sections="mentionSections"
        :loading="mentionLoading"
        :error="mentionError"
        @select="onMentionSelect"
      />
    </div>

    <ul
      v-if="media.length || tracks.length"
      class="status-composer__attachments"
    >
      <li
        v-for="entry in media"
        :key="entry.id || entry.name"
        class="status-composer__attachment"
      >
        <AppIcon name="paperclip" spacing="right" />
        <span class="status-composer__attachment-name">{{ entry.name }}</span>
        <span v-if="entry.uploading" class="status-composer__progress">
          {{ entry.progress }}%
        </span>
        <AppButton
          variant="ghost"
          size="sm"
          icon="xmark"
          :aria-label="t('common.delete')"
          :disabled="entry.uploading"
          @click="removeMedia(entry)"
        />
      </li>
      <li
        v-for="track in tracks"
        :key="track.id"
        class="status-composer__attachment"
      >
        <AppIcon name="music" spacing="right" />
        <span class="status-composer__attachment-name">
          {{ track.title
          }}<template v-if="track.subtitle"> — {{ track.subtitle }}</template>
        </span>
        <AppButton
          variant="ghost"
          size="sm"
          icon="xmark"
          :aria-label="t('common.delete')"
          @click="removeTrack(track.id)"
        />
      </li>
    </ul>

    <div class="status-composer__toolbar">
      <AppButton
        v-if="props.allowFileAttachments"
        type="button"
        variant="ghost"
        size="sm"
        icon="paperclip"
        :disabled="!canAttachMedia || submitting"
        :title="t('statusComposer.attachFile')"
        :aria-label="t('statusComposer.attachFile')"
        @click="triggerFileInput"
      />
      <AppButton
        v-if="props.allowTrackAttachments"
        type="button"
        variant="ghost"
        size="sm"
        icon="music"
        :disabled="!canAttachTrack || submitting"
        :title="t('statusComposer.attachTrack')"
        :aria-label="t('statusComposer.attachTrack')"
        @click="trackSearchOpen = !trackSearchOpen"
      />
      <input
        ref="fileInput"
        type="file"
        class="status-composer__file-input"
        @change="onFileChange"
      />
    </div>

    <div v-if="trackSearchOpen" class="status-composer__track-search">
      <SearchBar
        v-model="trackQuery"
        :autocomplete="true"
        :autocomplete-entities="['tracks']"
        :autocomplete-fetcher="trackAutocompleteFetcher"
        :autocomplete-min-length="1"
        :autocomplete-delay="300"
        :placeholder="t('statusComposer.trackSearchPlaceholder')"
        @select-suggestion="onTrackSelect"
      />
    </div>

    <div class="status-composer__options">
      <AppSelect
        v-model="contentType"
        :options="contentTypeOptions"
        :label="t('statusComposer.contentType')"
        :disabled="submitting"
      />
      <AppSelect
        v-model="visibility"
        :options="visibilityOptions"
        :label="t('statusComposer.visibility')"
        :disabled="submitting"
      />
      <div class="status-composer__language">
        <label class="status-composer__language-label" for="status-language">
          {{ t("statusComposer.language") }}
        </label>
        <input
          id="status-language"
          v-model="language"
          type="text"
          class="status-composer__language-input"
          list="status-language-options"
          maxlength="35"
          :disabled="submitting"
        />
        <datalist id="status-language-options">
          <option v-for="tag in languageOptions" :key="tag" :value="tag" />
        </datalist>
      </div>
    </div>

    <p v-if="error" class="status-composer__error" role="alert">{{ error }}</p>

    <div class="status-composer__actions">
      <AppButton
        type="submit"
        icon="paper-plane"
        :loading="submitting"
        :disabled="!canSubmit"
      >
        {{ props.submitLabel ?? t("statusComposer.publish") }}
      </AppButton>
    </div>
  </form>
</template>

<style scoped>
.status-composer {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.status-composer__editor {
  position: relative;
}

.status-composer__textarea {
  width: 100%;
  box-sizing: border-box;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-family: inherit;
  font-size: 1rem;
  resize: vertical;
}

.status-composer__textarea:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.status-composer__attachments {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.status-composer__attachment {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.status-composer__attachment-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-composer__progress {
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.status-composer__toolbar {
  display: flex;
  gap: var(--space-2);
}

.status-composer__file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.status-composer__options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: var(--space-3);
}

.status-composer__language {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.status-composer__language-label {
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 500;
}

.status-composer__language-input {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
}

.status-composer__error {
  margin: 0;
  color: var(--color-danger);
}

.status-composer__actions {
  display: flex;
  justify-content: flex-end;
}
</style>
