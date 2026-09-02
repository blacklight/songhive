<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, useRouter } from "vue-router";
import {
  listFiles,
  uploadFile,
  bulkUploadFiles,
  deleteFile,
  type FileUploadResult,
  type StoredFileResponse,
  type ExternalDuplicateWarning,
} from "@/api/files";
import { ApiError, getApiErrorMessage } from "@/api/client";
import { buildUrl } from "@/api/config";
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
import ExternalDuplicateModal from "@/components/external-libraries/ExternalDuplicateModal.vue";

const { t } = useI18n();
const router = useRouter();
const toast = useToastStore();

const visibility = ref<Visibility>("public");
const uploading = ref(false);
const isBulkUploading = ref(false);
const uploadError = ref<string | null>(null);
const uploadFailures = ref<{ name: string; message: string }[]>([]);
const selectedFiles = ref<File[]>([]);
const currentFileIndex = ref(0);
const currentFileProgress = ref(0);
const lastUploadTotal = ref(0);
const fileInput = ref<HTMLInputElement | null>(null);
const libraries = ref<LibraryResponse[]>([]);
const selectedLibraryId = ref("");
const uploadController = ref<AbortController | null>(null);
const uploadCancelled = ref(false);

const duplicateOpen = ref(false);
const duplicateWarning = ref<ExternalDuplicateWarning | null>(null);
let duplicateResolve: ((value: unknown | null) => void) | null = null;

function isTrackResponse(
  value: unknown,
): value is { id: string; title: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof (value as { id: unknown }).id === "string" &&
    "title" in value
  );
}

function waitForDuplicate(
  warning: ExternalDuplicateWarning,
): Promise<unknown | null> {
  duplicateWarning.value = warning;
  duplicateOpen.value = true;
  return new Promise((resolve) => {
    duplicateResolve = resolve;
  });
}

function onDuplicateResolved(result: unknown) {
  duplicateOpen.value = false;
  duplicateWarning.value = null;
  if (duplicateResolve) {
    duplicateResolve(result);
    duplicateResolve = null;
  }
}

function onDuplicateClosed() {
  duplicateOpen.value = false;
  duplicateWarning.value = null;
  if (duplicateResolve) {
    duplicateResolve(null);
    duplicateResolve = null;
  }
}

function cancelUpload() {
  uploadCancelled.value = true;
  onDuplicateClosed();
  uploadController.value?.abort();
}

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
  if (isBulkUploading.value) {
    return currentFileProgress.value;
  }
  const completed = currentFileIndex.value * 100;
  const current = currentFileProgress.value;
  return Math.round((completed + current) / selectedFiles.value.length);
});

const progressLabel = computed(() => {
  if (isBulkUploading.value) {
    if (currentFileProgress.value > 0) {
      return t("pages.files.uploadProgress", {
        percent: currentFileProgress.value,
      });
    }
    return t("pages.files.uploading");
  }
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

function isImage(file: StoredFileResponse): boolean {
  return file.content_type.startsWith("image/");
}

function isAudio(file: StoredFileResponse): boolean {
  return file.content_type.startsWith("audio/");
}

function previewUrl(file: StoredFileResponse): string {
  return buildUrl(file.url, { disposition: "inline" });
}

const currentAudio = ref<HTMLAudioElement | null>(null);
const playingFileId = ref<string | null>(null);

function stopAudio() {
  currentAudio.value?.pause();
  currentAudio.value = null;
  playingFileId.value = null;
}

function toggleAudio(file: StoredFileResponse, event?: MouseEvent) {
  event?.stopPropagation();
  if (playingFileId.value === file.id) {
    stopAudio();
    return;
  }

  stopAudio();
  const audio = new Audio(previewUrl(file));
  audio.onended = () => {
    if (playingFileId.value === file.id) {
      stopAudio();
    }
  };
  currentAudio.value = audio;
  playingFileId.value = file.id;
  void audio.play();
}

onUnmounted(() => {
  stopAudio();
});

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
  uploadCancelled.value = false;
  uploading.value = true;
  isBulkUploading.value = files.length > 1;
  currentFileIndex.value = 0;
  currentFileProgress.value = 0;
  selectedFiles.value = files;
  lastUploadTotal.value = files.length;

  const controller = new AbortController();
  uploadController.value = controller;
  const signal = controller.signal;

  const results: FileUploadResult[] = [];
  const libraryId = selectedLibraryId.value || undefined;

  function addResolvedResult(result: unknown, filename: string) {
    if (isTrackResponse(result)) {
      results.push({ id: result.id, trackId: result.id } as FileUploadResult);
    } else if (result && typeof result === "object" && "id" in result) {
      results.push({ ...(result as StoredFileResponse) } as FileUploadResult);
    } else {
      uploadFailures.value.push({
        name: filename,
        message: t("pages.files.uploadError", { message: t("errors.unknown") }),
      });
    }
  }

  try {
    if (files.length === 1) {
      const file = files[0];
      currentFileIndex.value = 0;
      try {
        const result = await uploadFile(
          file,
          visibility.value,
          (percent) => {
            currentFileProgress.value = percent;
          },
          libraryId,
          signal,
        );
        results.push(result);
      } catch (err) {
        if (uploadCancelled.value) {
          uploadError.value = t("pages.files.uploadCancelled");
          return;
        }
        if (
          err instanceof ApiError &&
          err.status === 409 &&
          err.body &&
          typeof err.body === "object" &&
          "token" in err.body
        ) {
          const resolved = await waitForDuplicate(
            err.body as ExternalDuplicateWarning,
          );
          if (uploadCancelled.value) {
            uploadError.value = t("pages.files.uploadCancelled");
            return;
          }
          if (resolved) {
            addResolvedResult(resolved, file.name);
          } else {
            uploadFailures.value.push({
              name: file.name,
              message: t("pages.files.uploadError", {
                message: t("errors.unknown"),
              }),
            });
          }
        } else {
          uploadFailures.value.push({
            name: file.name,
            message: getErrorMessage(err),
          });
        }
      } finally {
        currentFileProgress.value = 100;
      }
    } else {
      try {
        const bulkResults = await bulkUploadFiles(
          files,
          visibility.value,
          (percent) => {
            currentFileProgress.value = percent;
          },
          libraryId,
          signal,
        );
        for (const [index, item] of bulkResults.entries()) {
          if (uploadCancelled.value) {
            break;
          }
          if (item.status === "external_duplicate" && item.external_duplicate) {
            const resolved = await waitForDuplicate(item.external_duplicate);
            if (uploadCancelled.value) {
              uploadError.value = t("pages.files.uploadCancelled");
              return;
            }
            if (resolved) {
              addResolvedResult(resolved, item.filename ?? files[index].name);
            } else {
              uploadFailures.value.push({
                name: item.filename ?? files[index].name,
                message: t("pages.files.uploadError", {
                  message: t("errors.unknown"),
                }),
              });
            }
          } else if (item.error) {
            uploadFailures.value.push({
              name: item.filename ?? files[index].name,
              message: item.error,
            });
          } else if (item.stored_file) {
            results.push({
              ...item.stored_file,
              trackId: item.track_id,
            });
          }
        }
      } catch (err) {
        if (uploadCancelled.value) {
          uploadError.value = t("pages.files.uploadCancelled");
          return;
        }
        const message = getErrorMessage(err);
        for (const file of files) {
          uploadFailures.value.push({ name: file.name, message });
        }
      } finally {
        currentFileProgress.value = 100;
      }
    }

    if (uploadCancelled.value) {
      uploadError.value = t("pages.files.uploadCancelled");
      return;
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
    isBulkUploading.value = false;
    selectedFiles.value = [];
    currentFileIndex.value = 0;
    currentFileProgress.value = 0;
    uploadController.value = null;
    uploadCancelled.value = false;
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

      <div v-if="uploading" class="files-view__progress-actions">
        <AppButton
          class="files-view__cancel"
          icon="xmark"
          variant="danger"
          size="sm"
          :title="t('common.cancel')"
          @click="cancelUpload"
        >
          {{ t("common.cancel") }}
        </AppButton>
      </div>

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
        <div
          class="files-view__card"
          :class="{ 'files-view__card--bulk': bulkMode }"
        >
          <img
            v-if="isImage(item)"
            :src="previewUrl(item)"
            :alt="fileName(item)"
            class="files-view__thumb"
          />
          <button
            v-else-if="isAudio(item)"
            type="button"
            class="files-view__play"
            :aria-label="
              playingFileId === item.id ? t('common.pause') : t('common.play')
            "
            @click="(e) => toggleAudio(item, e)"
          >
            <AppIcon :name="playingFileId === item.id ? 'pause' : 'play'" />
          </button>
          <AppIcon v-else :name="fileIcon(item)" class="files-view__icon" />

          <RouterLink
            v-if="!bulkMode"
            :to="{ name: 'file', params: { id: item.id } }"
            class="files-view__name"
          >
            {{ fileName(item) }}
          </RouterLink>
          <span v-else class="files-view__name">{{ fileName(item) }}</span>

          <span class="files-view__meta">
            {{ formatBytes(item.size) }} ·
            {{ t(`browse.visibility.${toVisibility(item.visibility)}`) }}
          </span>
        </div>
      </template>
    </BulkEditableGrid>

    <ExternalDuplicateModal
      :open="duplicateOpen"
      :warning="duplicateWarning"
      @close="onDuplicateClosed"
      @resolved="onDuplicateResolved"
    />
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
  display: grid;
  grid-template-columns: 3rem 1fr;
  grid-template-areas:
    "preview name"
    "preview meta";
  gap: var(--space-1) var(--space-3);
  align-items: start;
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
  grid-area: name;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: var(--color-text);
  text-decoration: none;
  align-self: end;
}

.files-view__name:hover {
  text-decoration: underline;
}

.files-view__thumb,
.files-view__play,
.files-view__icon {
  grid-area: preview;
  width: 3rem;
  height: 3rem;
  border-radius: var(--radius-sm);
  align-self: start;
}

.files-view__thumb {
  object-fit: cover;
  background-color: var(--color-surface-raised);
}

.files-view__play {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-secondary);
  font-size: 1.125rem;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.files-view__play:hover {
  background-color: var(--color-surface-hover);
  color: var(--color-text-hover);
}

.files-view__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.25rem;
  color: var(--color-text-muted);
}

.files-view__meta {
  grid-area: meta;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  white-space: nowrap;
  align-self: start;
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
  background-color: var(--color-surface-active);
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

.files-view__progress-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-2);
}

.files-view__cancel {
  flex-shrink: 0;
}
</style>
