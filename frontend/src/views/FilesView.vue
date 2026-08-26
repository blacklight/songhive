<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, useRouter } from "vue-router";
import {
  listFiles,
  uploadFile,
  deleteFile,
  type StoredFileResponse,
} from "@/api/files";
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
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";

const { t } = useI18n();
const router = useRouter();
const toast = useToastStore();

const visibility = ref<Visibility>("public");
const uploading = ref(false);
const progress = ref(0);
const uploadError = ref<string | null>(null);
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
  refresh,
} = useEntityList<StoredFileResponse>(listFiles);

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

async function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) {
    resetInput();
    return;
  }

  uploadError.value = null;
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
    uploadError.value = t("pages.files.uploadError", {
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

      <div v-if="uploadError" class="files-view__error" role="alert">
        <span>{{ uploadError }}</span>
      </div>
    </section>

    <BulkEditableGrid
      :title="t('pages.files.title')"
      icon="file"
      :items="files"
      :loading="loading"
      :error="listError"
      :has-more="hasMore"
      :query="query"
      :entity-singular="t('browse.entities.file')"
      :entity-plural="t('browse.entities.files')"
      :delete-one="(id: string) => deleteFile(id)"
      :refresh="refresh"
      :get-name="fileName"
      :search="search"
      :load-more="loadMore"
      :retry="retry"
      layout="list"
      item-class="files-view__item"
    >
      <template #card="{ item, bulkMode }">
        <RouterLink
          :to="{ name: 'file', params: { id: item.id } }"
          class="files-view__card"
          :class="{ 'files-view__card--bulk': bulkMode }"
        >
          <AppIcon :name="fileIcon(item)" spacing="right" />
          <span class="files-view__name">{{ fileName(item) }}</span>
          <span class="files-view__meta">
            {{ formatBytes(item.size) }} ·
            {{ t(`browse.visibility.${toVisibility(item.visibility)}`) }}
          </span>
        </RouterLink>
      </template>
    </BulkEditableGrid>
  </div>
</template>

<style scoped>
.files-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;
}

.files-view__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.files-view__item.bulk-editable-grid__item-wrapper--list {
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  transition: background-color var(--transition-fast);
  padding: var(--space-2) 2.5rem var(--space-2) var(--space-3);
}

.files-view__item.bulk-editable-grid__item-wrapper--list:hover {
  background-color: var(--color-bg-hover);
}

.files-view__card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
  color: var(--color-text);
  text-decoration: none;
  padding: var(--space-1) 0;
}

.files-view__card--bulk {
  pointer-events: none;
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
