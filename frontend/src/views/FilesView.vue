<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, useRouter } from "vue-router";
import { listFiles, uploadFile, type StoredFileResponse } from "@/api/files";
import { getApiErrorMessage } from "@/api/client";
import {
  listLibraries,
  type Visibility,
  type LibraryResponse,
} from "@/api/libraries";
import { useEntityList } from "@/composables/useEntityList";
import { useToastStore } from "@/stores/toast";
import { formatBytes, toVisibility } from "@/utils/entity";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const router = useRouter();
const toast = useToastStore();

const visibility = ref<Visibility>("public");
const uploading = ref(false);
const progress = ref(0);
const error = ref<string | null>(null);
const selectedFileName = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const libraries = ref<LibraryResponse[]>([]);
const selectedLibraryId = ref("");

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

const writableLibraries = computed(() =>
  libraries.value.filter((library) => library.can_write),
);

const libraryOptions = computed(() => [
  { value: "", label: t("pages.files.defaultLibrary") },
  ...writableLibraries.value.map((library) => ({
    value: library.id,
    label: library.name,
  })),
]);

const chooseFileLabel = computed(() =>
  selectedFileName.value ? selectedFileName.value : t("pages.files.selectFile"),
);

const progressLabel = computed(() =>
  progress.value > 0
    ? t("pages.files.uploadProgress", { percent: progress.value })
    : t("pages.files.uploading"),
);

const {
  items: files,
  loading,
  error: listError,
  query,
  hasMore,
  load,
  loadMore,
  search,
  retry,
} = useEntityList<StoredFileResponse>(listFiles);

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

onMounted(async () => {
  try {
    libraries.value = await listLibraries();
  } catch {
    // The upload can still fall back to the default library on the backend.
    libraries.value = [];
  }
  await load();
});

function resetInput() {
  progress.value = 0;
  if (fileInput.value) {
    fileInput.value.value = "";
  }
}

function onSelectClick() {
  fileInput.value?.click();
}

function fileName(file: StoredFileResponse): string {
  return file.original_filename?.trim()
    ? file.original_filename
    : t("pages.files.untitledFile");
}

function fileIcon(file: StoredFileResponse): string {
  if (file.content_type.startsWith("audio/")) return "music";
  if (file.content_type.startsWith("image/")) return "image";
  if (file.content_type.startsWith("video/")) return "video";
  return "file";
}

async function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) {
    resetInput();
    return;
  }

  error.value = null;
  uploading.value = true;
  progress.value = 0;
  selectedFileName.value = file.name;

  try {
    const libraryId = selectedLibraryId.value || undefined;
    const response = await uploadFile(
      file,
      visibility.value,
      (percent) => {
        progress.value = percent;
      },
      libraryId,
    );
    toast.push({ type: "success", message: t("pages.files.uploadSuccess") });
    if (response.trackId) {
      await router.push({ name: "track", params: { id: response.trackId } });
    } else {
      await router.push({ name: "file", params: { id: response.id } });
    }
  } catch (err) {
    error.value = t("pages.files.uploadError", {
      message: getErrorMessage(err),
    });
  } finally {
    uploading.value = false;
    selectedFileName.value = null;
    resetInput();
  }
}
</script>

<template>
  <div class="files-view">
    <AppPageTitle class="files-view__title" icon="file">{{
      t("pages.files.title")
    }}</AppPageTitle>

    <SearchBar
      :model-value="query"
      :debounce="0"
      class="files-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.files'),
        })
      "
      @update:model-value="search"
    />

    <section
      class="boxed files-view__upload"
      aria-labelledby="files-upload-heading"
    >
      <AppPageTitle
        id="files-upload-heading"
        :level="2"
        class="files-view__section-title"
        icon="upload"
      >
        {{ t("pages.files.uploadTitle") }}
      </AppPageTitle>

      <div class="files-view__controls">
        <AppSelect
          v-model="visibility"
          :label="t('pages.files.visibility')"
          :options="visibilityOptions"
          :disabled="uploading"
        />

        <AppSelect
          v-model="selectedLibraryId"
          :label="t('pages.files.library')"
          :options="libraryOptions"
          :disabled="uploading"
        />

        <AppButton
          icon="folder-open"
          variant="secondary"
          :loading="uploading"
          :disabled="uploading"
          @click="onSelectClick"
        >
          {{ chooseFileLabel }}
        </AppButton>

        <input
          ref="fileInput"
          type="file"
          class="files-view__file-input"
          @change="onFileChange"
        />
      </div>

      <div
        v-if="uploading"
        class="files-view__progress"
        role="progressbar"
        :aria-valuenow="progress"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-label="progressLabel"
      >
        <div
          class="files-view__progress-bar"
          :style="{ width: `${progress}%` }"
        />
      </div>

      <p v-if="uploading && progress > 0" class="files-view__progress-text">
        {{ progressLabel }}
      </p>

      <div v-if="error" class="files-view__error" role="alert">
        <span>{{ error }}</span>
      </div>
    </section>
    <div v-if="listError" class="files-view__error" role="alert">
      <span>{{ listError }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="loading && files.length === 0"
      class="files-view__list files-view__list--skeleton"
    >
      <SkeletonLoader v-for="i in 5" :key="i" variant="list-row" />
    </div>

    <p v-else-if="files.length === 0" class="files-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.files") }) }}
    </p>

    <ul v-else class="files-view__list">
      <li v-for="file in files" :key="file.id" class="files-view__item">
        <RouterLink
          :to="{ name: 'file', params: { id: file.id } }"
          class="files-view__link"
        >
          <AppIcon :name="fileIcon(file)" spacing="right" />
          <span class="files-view__name">{{ fileName(file) }}</span>
          <span class="files-view__meta">
            {{ formatBytes(file.size) }} ·
            {{ t(`browse.visibility.${toVisibility(file.visibility)}`) }}
          </span>
        </RouterLink>
      </li>
    </ul>

    <div class="files-view__footer">
      <AppButton
        v-if="hasMore"
        icon="chevron-down"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
      <AppSpinner v-else-if="loading" />
    </div>
  </div>
</template>

<style scoped>
.files-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;
}

.files-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.files-view__search {
  max-width: 32rem;
}

.files-view__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.files-view__list--skeleton {
  gap: var(--space-3);
}

.files-view__item {
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  transition: background-color var(--transition-fast);
}

.files-view__item:hover {
  background-color: var(--color-bg-hover);
}

.files-view__link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  color: var(--color-text);
  text-decoration: none;
}

.files-view__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.files-view__meta {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.files-view__empty {
  padding: var(--space-8);
  text-align: center;
  color: var(--color-text-muted);
}

.files-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
  font-size: 0.9375rem;
}

.files-view__footer {
  display: flex;
  justify-content: center;
}

.files-view__upload {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.files-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.files-view__controls {
  display: flex;
  align-items: end;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.files-view__file-input {
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

.files-view__progress {
  width: 100%;
  height: 0.75rem;
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
  overflow: hidden;
}

.files-view__progress-bar {
  height: 100%;
  background-color: var(--color-accent);
  transition: width 0.1s linear;
}

.files-view__progress-text {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}
</style>
