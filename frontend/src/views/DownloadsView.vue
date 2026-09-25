<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  clearCompletedArchives,
  deleteArchive,
  downloadArchiveFile,
  listArchives,
  type DownloadArchive,
} from "@/api/downloads";
import { getApiErrorMessage } from "@/api/client";
import { formatDateTime } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import { useConfirm } from "@/composables/useConfirm";
import { formatBytes } from "@/utils/entity";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const POLL_MS = 5000;

const { t } = useI18n();
const toast = useToastStore();
const { confirm } = useConfirm();

const archives = ref<DownloadArchive[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const busy = ref(false);

let pollTimer: ReturnType<typeof setTimeout> | null = null;

const hasActive = computed(() =>
  archives.value.some(
    (archive) =>
      archive.status === "pending" || archive.status === "processing",
  ),
);

const completedCount = computed(
  () =>
    archives.value.filter(
      (archive) => archive.status === "ready" || archive.status === "failed",
    ).length,
);

async function load() {
  try {
    archives.value = await listArchives();
    error.value = null;
  } catch (err) {
    error.value = getApiErrorMessage(err);
  }
}

function schedulePoll() {
  stopPoll();
  if (hasActive.value) {
    pollTimer = setTimeout(() => {
      void load().then(schedulePoll);
    }, POLL_MS);
  }
}

function stopPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function statusIcon(archive: DownloadArchive): string {
  switch (archive.status) {
    case "ready":
      return "circle-check";
    case "failed":
      return "circle-exclamation";
    case "processing":
      return "spinner";
    default:
      return "clock";
  }
}

function statusLabel(archive: DownloadArchive): string {
  return t(`downloads.status.${archive.status}`, archive.status);
}

function statusClass(archive: DownloadArchive): string {
  return `downloads-view__status--${archive.status}`;
}

async function onDownload(archive: DownloadArchive) {
  try {
    await downloadArchiveFile(archive);
  } catch (err) {
    toast.push({
      type: "error",
      message: t("downloads.downloadFailed", {
        message: getApiErrorMessage(err),
      }),
    });
  }
}

async function onDelete(archive: DownloadArchive) {
  try {
    await deleteArchive(archive.id);
    archives.value = archives.value.filter((row) => row.id !== archive.id);
  } catch (err) {
    toast.push({
      type: "error",
      message: t("downloads.deleteFailed", {
        message: getApiErrorMessage(err),
      }),
    });
  }
}

async function onClearCompleted() {
  const ok = await confirm({
    title: t("downloads.clearCompleted"),
    message: t("downloads.clearCompletedConfirm"),
    confirmLabel: t("downloads.clearCompleted"),
  });
  if (!ok) return;
  busy.value = true;
  try {
    const result = await clearCompletedArchives();
    await load();
    toast.push({
      type: "success",
      message: t("downloads.cleared", { count: result.cleared }),
    });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("downloads.deleteFailed", {
        message: getApiErrorMessage(err),
      }),
    });
  } finally {
    busy.value = false;
  }
}

async function onRefresh() {
  loading.value = true;
  try {
    await load();
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  loading.value = true;
  try {
    await load();
  } finally {
    loading.value = false;
  }
  schedulePoll();
});

onUnmounted(stopPoll);

// Re-arm polling whenever the active set changes (e.g. a pending archive
// appears after a refresh).
watch(hasActive, () => schedulePoll());
</script>

<template>
  <div class="downloads-view">
    <AppPageTitle icon="download">{{ t("downloads.title") }}</AppPageTitle>

    <div class="downloads-view__toolbar">
      <AppButton
        size="sm"
        variant="secondary"
        icon="rotate-right"
        :loading="loading"
        @click="onRefresh"
        >{{ t("downloads.refresh") }}</AppButton
      >
      <AppButton
        v-if="completedCount > 0"
        size="sm"
        variant="secondary"
        icon="broom"
        :loading="busy"
        @click="onClearCompleted"
        >{{ t("downloads.clearCompleted") }}</AppButton
      >
    </div>

    <p v-if="error" class="downloads-view__error">{{ error }}</p>

    <SkeletonLoader v-if="loading && archives.length === 0" />

    <p
      v-else-if="archives.length === 0 && !error"
      class="downloads-view__empty"
    >
      {{ t("downloads.empty") }}
    </p>

    <ul v-else class="downloads-view__list">
      <li
        v-for="archive in archives"
        :key="archive.id"
        class="downloads-view__row"
      >
        <div class="downloads-view__main">
          <span class="downloads-view__label">{{ archive.label }}</span>
          <span class="downloads-view__meta">
            {{ t("downloads.itemCount", { count: archive.item_count }) }}
            <template v-if="archive.size">
              · {{ formatBytes(archive.size) }}</template
            >
            · {{ formatDateTime(archive.created_at) }}
          </span>
          <span
            v-if="archive.item_errors?.length"
            class="downloads-view__warning"
          >
            {{
              t("downloads.itemErrors", { count: archive.item_errors.length })
            }}
          </span>
          <span v-if="archive.error" class="downloads-view__error-text">
            {{ archive.error }}
          </span>
        </div>
        <div class="downloads-view__side">
          <span class="downloads-view__status" :class="statusClass(archive)">
            <AppIcon :name="statusIcon(archive)" />
            {{ statusLabel(archive) }}
          </span>
          <AppButton
            v-if="archive.status === 'ready'"
            size="sm"
            icon="download"
            @click="onDownload(archive)"
            >{{ t("common.download") }}</AppButton
          >
          <AppButton
            size="sm"
            variant="ghost"
            icon="trash"
            @click="onDelete(archive)"
            >{{ t("common.delete") }}</AppButton
          >
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.downloads-view {
  padding: 1rem;
  max-width: 60rem;
  margin: 0 auto;
}

.downloads-view__toolbar {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.downloads-view__error,
.downloads-view__empty {
  color: var(--text-muted, #6b7280);
}

.downloads-view__error {
  color: var(--danger, #dc2626);
}

.downloads-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.downloads-view__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--border-color, #374151);
  border-radius: 0.5rem;
  background: var(--bg-secondary, rgba(255, 255, 255, 0.03));
}

.downloads-view__main {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 0;
}

.downloads-view__label {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.downloads-view__meta {
  font-size: 0.8rem;
  color: var(--text-muted, #9ca3af);
}

.downloads-view__warning {
  font-size: 0.8rem;
  color: var(--warning, #d97706);
}

.downloads-view__error-text {
  font-size: 0.8rem;
  color: var(--danger, #dc2626);
}

.downloads-view__side {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.downloads-view__status {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.8rem;
  color: var(--text-muted, #9ca3af);
}

.downloads-view__status--ready {
  color: var(--success, #16a34a);
}

.downloads-view__status--failed {
  color: var(--danger, #dc2626);
}
</style>
