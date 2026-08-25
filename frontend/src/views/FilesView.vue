<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { uploadFile } from "@/api/files";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import type { Visibility } from "@/api/libraries";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSelect from "@/components/ui/AppSelect.vue";

const { t } = useI18n();
const router = useRouter();
const toast = useToastStore();

const visibility = ref<Visibility>("public");
const uploading = ref(false);
const progress = ref(0);
const error = ref<string | null>(null);
const selectedFileName = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

const chooseFileLabel = computed(() =>
  selectedFileName.value ? selectedFileName.value : t("pages.files.selectFile"),
);

const progressLabel = computed(() =>
  progress.value > 0
    ? t("pages.files.uploadProgress", { percent: progress.value })
    : t("pages.files.uploading"),
);

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

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

  error.value = null;
  uploading.value = true;
  progress.value = 0;
  selectedFileName.value = file.name;

  try {
    const response = await uploadFile(file, visibility.value, (percent) => {
      progress.value = percent;
    });
    toast.push({ type: "success", message: t("pages.files.uploadSuccess") });
    await router.push({ name: "file", params: { id: response.id } });
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

    <p class="files-view__notice" role="note">
      {{ t("pages.files.noList") }}
    </p>

    <section class="files-view__upload" aria-labelledby="files-upload-heading">
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

.files-view__notice {
  margin: 0;
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text-muted);
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

.files-view__error {
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
  color: var(--color-danger);
  font-size: 0.9375rem;
}
</style>
