<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { getFile, type StoredFileResponse } from "@/api/files";
import { getApiErrorMessage } from "@/api/client";
import { API_PREFIX, buildUrl } from "@/api/config";
import { useToastStore } from "@/stores/toast";
import { formatBytes, toVisibility } from "@/utils/entity";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const toast = useToastStore();

const file = ref<StoredFileResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const fileId = computed(() => String(route.params.id));

const displayName = computed(() =>
  file.value?.original_filename?.trim()
    ? file.value.original_filename
    : t("pages.files.untitledFile"),
);

const downloadUrl = computed(() =>
  file.value
    ? buildUrl(`${API_PREFIX}/files/${file.value.id}/download`, {
        disposition: "attachment",
      })
    : undefined,
);

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function load() {
  loading.value = true;
  error.value = null;
  file.value = null;

  try {
    file.value = await getFile(fileId.value);
  } catch (err) {
    error.value = getErrorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function copySha256() {
  if (!file.value) return;

  try {
    await navigator.clipboard.writeText(file.value.sha256);
    toast.push({ type: "success", message: t("pages.files.sha256Copied") });
  } catch {
    toast.push({ type: "error", message: t("pages.files.copyFailed") });
  }
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="file-detail-view">
    <div v-if="loading && !file" class="file-detail-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="file-detail-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else-if="file">
      <AppPageTitle class="file-detail-view__title" icon="file">{{
        displayName
      }}</AppPageTitle>

      <section class="file-detail-view__meta">
        <div class="file-detail-view__row">
          <span class="file-detail-view__label">
            {{ t("pages.files.originalFilename") }}
          </span>
          <span class="file-detail-view__value">{{ displayName }}</span>
        </div>

        <div class="file-detail-view__row">
          <span class="file-detail-view__label">
            {{ t("pages.files.contentType") }}
          </span>
          <span class="file-detail-view__value">{{ file.content_type }}</span>
        </div>

        <div class="file-detail-view__row">
          <span class="file-detail-view__label">{{
            t("pages.files.size")
          }}</span>
          <span class="file-detail-view__value">
            {{ formatBytes(file.size) }}
          </span>
        </div>

        <div class="file-detail-view__row">
          <span class="file-detail-view__label">
            {{ t("pages.files.sha256") }}
          </span>
          <div class="file-detail-view__sha256">
            <code class="file-detail-view__sha256-value" :title="file.sha256">
              {{ file.sha256 }}
            </code>
            <AppButton size="sm" icon="copy" @click="copySha256">
              {{ t("common.copy") }}
            </AppButton>
          </div>
        </div>

        <div class="file-detail-view__row">
          <span class="file-detail-view__label">
            {{ t("browse.detail.visibility") }}
          </span>
          <span class="file-detail-view__value">
            {{ t(`browse.visibility.${toVisibility(file.visibility)}`) }}
          </span>
        </div>

        <div v-if="file.owner_id" class="file-detail-view__row">
          <span class="file-detail-view__label">
            {{ t("browse.detail.owner") }}
          </span>
          <span class="file-detail-view__value">{{ file.owner_id }}</span>
        </div>

        <div class="file-detail-view__actions">
          <a
            :href="downloadUrl"
            target="_blank"
            :download="file.original_filename ?? file.id"
            class="file-detail-view__download"
          >
            <AppIcon name="download" spacing="right" />
            {{ t("pages.files.download") }}
          </a>
        </div>
      </section>
    </template>

    <div v-else class="file-detail-view__empty" role="alert">
      {{ t("pages.files.empty") }}
    </div>
  </div>
</template>

<style scoped>
.file-detail-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;
}

.file-detail-view__title {
  margin: 0;
  font-size: 1.5rem;
  word-break: break-word;
}

.file-detail-view__skeleton {
  min-height: 16rem;
}

.file-detail-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.file-detail-view__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.file-detail-view__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.file-detail-view__row {
  display: grid;
  grid-template-columns: 12rem 1fr;
  gap: var(--space-3);
  align-items: start;
}

@media (max-width: 640px) {
  .file-detail-view__row {
    grid-template-columns: 1fr;
    gap: var(--space-1);
  }
}

.file-detail-view__label {
  color: var(--color-text-muted);
  font-size: 0.9375rem;
}

.file-detail-view__value {
  word-break: break-word;
}

.file-detail-view__sha256 {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.file-detail-view__sha256-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.875rem;
  color: var(--color-text);
  background: transparent;
  min-width: 0;
}

.file-detail-view__actions {
  display: flex;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.file-detail-view__download {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
  font-weight: 500;
  text-decoration: none;
  cursor: pointer;
}

.file-detail-view__download:hover {
  filter: brightness(0.95);
}
</style>
