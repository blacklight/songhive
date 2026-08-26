<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, useRouter } from "vue-router";
import {
  listFiles,
  uploadFile,
  deleteFile,
  type FileUploadResult,
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
const uploadError = ref<string | null>(null);
const uploadFailures = ref<{ name: string; message: string }[]>([]);
const selectedFiles = ref<File[]>([]);
const currentFileIndex = ref(0);
const currentFileProgress = ref(0);
const lastUploadTotal = ref(0);
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

const chooseFileLabel = computed(() => {
  if (uploading.value) return t("pages.files.uploading");
  if (selectedFiles.value.length === 1) return selectedFiles.value[0].name;
  if (selectedFiles.value.length > 1) {
    return t("pages.files.selectedFiles", {
      count: selectedFiles.value.length,
    });
  }
  return t("pages.files.selectFile");
});

const overallProgress = computed(() => {
  if (selectedFiles.value.length === 0) return 0;
  const completed = currentFileIndex.value * 100;
  const current = currentFileProgress.value;
  return Math.round((completed + current) / selectedFiles.value.length);
});

const progressLabel = computed(() => {
  if (
    selectedFiles.value.length > 1 &&
    currentFileIndex.value < selectedFiles.value.length
  ) {
    const file = selectedFiles.value[currentFileIndex.value];
    return t("pages.files.uploadingFile", {
      current: currentFileIndex.value + 1,
      total: selectedFiles.value.length,
      name: file?.name ?? "",
      percent: overallProgress.value,
    });
  }
  if (currentFileProgress.value > 0) {
    return t("pages.files.uploadProgress", {
      percent: currentFileProgress.value,
    });
  }
  return t("pages.files.uploading");
});

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
  currentFileProgress.value = 0;
  if (fileInput.value) {
    fileInput.value.value = "";
  }
}

function onSelectClick() {
  fileInput.value?.click();
}

async function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const files = target.files ? Array.from(target.files) : [];
  if (files.length === 0) {
    resetInput();
    return;
  }

  uploadError.value = null;
  uploadFailures.value = [];
  uploading.value = true;
  currentFileIndex.value = 0;
  currentFileProgress.value = 0;
  selectedFiles.value = files;
  lastUploadTotal.value = files.length;

  const results: FileUploadResult[] = [];
  const libraryId = selectedLibraryId.value || undefined;

  try {
    for (let i = 0; i < files.length; i++) {
      currentFileIndex.value = i;
      currentFileProgress.value = 0;
      const file = files[i];
      try {
        const result = await uploadFile(
          file,
          visibility.value,
          (percent) => {
            currentFileProgress.value = percent;
          },
          libraryId,
        );
        results.push(result);
      } catch (err) {
        uploadFailures.value.push({
          name: file.name,
          message: getErrorMessage(err),
        });
      } finally {
        currentFileProgress.value = 100;
      }
    }

    if (uploadFailures.value.length === 0) {
      toast.push({
        type: "success",
        message:
          results.length === 1
            ? t("pages.files.uploadSuccess")
            : t("pages.files.uploadSuccessPlural", { count: results.length }),
      });
    } else if (results.length === 0) {
      toast.push({
        type: "error",
        message:
          files.length === 1
            ? t("pages.files.uploadError", {
                message: uploadFailures.value[0].message,
              })
            : t("pages.files.uploadAllFailed", { count: files.length }),
      });
    } else {
      toast.push({
        type: "warning",
        message: t("pages.files.uploadPartial", {
          success: results.length,
          total: files.length,
        }),
      });
    }

    if (uploadFailures.value.length === 0 && results.length === 1) {
      if (results[0].trackId) {
        await router.push({
          name: "track",
          params: { id: results[0].trackId },
        });
      } else {
        await router.push({ name: "file", params: { id: results[0].id } });
      }
    } else {
      await refresh();
    }

    if (uploadFailures.value.length > 0) {
      uploadError.value =
        files.length === 1
          ? t("pages.files.uploadError", {
              message: uploadFailures.value[0].message,
            })
          : t("pages.files.uploadPartial", {
              success: results.length,
              total: files.length,
            });
    }
  } finally {
    uploading.value = false;
    selectedFiles.value = [];
    currentFileIndex.value = 0;
    currentFileProgress.value = 0;
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
          multiple
          class="files-view__file-input"
          @change="onFileChange"
        />
      </div>

      <div
        v-if="uploading"
        class="files-view__progress"
        role="progressbar"
        :aria-valuenow="overallProgress"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-label="progressLabel"
      >
        <div
          class="files-view__progress-bar"
          :style="{ width: `${overallProgress}%` }"
        />
      </div>

      <p v-if="uploading" class="files-view__progress-text">
        {{ progressLabel }}
      </p>

      <div v-if="uploadError" class="files-view__error" role="alert">
        <span>{{ uploadError }}</span>
      </div>

      <ul
        v-if="uploadFailures.length > 0 && lastUploadTotal > 1"
        class="files-view__error-list"
        role="list"
      >
        <li
          v-for="(failure, index) in uploadFailures"
          :key="`${failure.name}-${index}`"
        >
          {{
            t("pages.files.fileUploadError", {
              name: failure.name,
              message: failure.message,
            })
          }}
        </li>
      </ul>
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
  background-color: var(--color-surface-hover);
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

.files-view__error-list {
  margin: 0;
  padding: 0 0 0 var(--space-5);
  color: var(--color-danger);
  font-size: 0.9375rem;
}

.files-view__error-list li {
  margin-bottom: var(--space-1);
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
