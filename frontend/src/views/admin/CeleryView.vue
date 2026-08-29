<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useConfirm } from "@/composables/useConfirm";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import {
  listCeleryTasks,
  terminateCeleryTasks,
  type CeleryTaskInfo,
} from "@/api/admin";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const { confirm } = useConfirm();

const tasks = ref<CeleryTaskInfo[]>([]);
const loading = ref(false);
const terminating = ref(false);
const error = ref<string | null>(null);

const selectedIds = ref<Set<string>>(new Set());

const allSelected = computed(
  () => tasks.value.length > 0 && selectedIds.value.size === tasks.value.length,
);

const someSelected = computed(
  () =>
    selectedIds.value.size > 0 && selectedIds.value.size < tasks.value.length,
);

const selectedCount = computed(() => selectedIds.value.size);

function isSelected(task: CeleryTaskInfo): boolean {
  return selectedIds.value.has(task.task_id);
}

function toggleSelect(task: CeleryTaskInfo, value: boolean) {
  if (value) {
    selectedIds.value.add(task.task_id);
  } else {
    selectedIds.value.delete(task.task_id);
  }
  selectedIds.value = new Set(selectedIds.value);
}

function toggleSelectAll(value: boolean) {
  if (value) {
    selectedIds.value = new Set(tasks.value.map((task) => task.task_id));
  } else {
    selectedIds.value.clear();
    selectedIds.value = new Set();
  }
}

function showError(messageKey: string, err: unknown) {
  toastStore.push({
    type: "error",
    message: t(messageKey, {
      message: getApiErrorMessage(err) || t("errors.unknown"),
    }),
  });
}

async function load() {
  if (loading.value) return;
  loading.value = true;
  error.value = null;

  try {
    tasks.value = await listCeleryTasks();
    selectedIds.value.clear();
    selectedIds.value = new Set();
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
    showError("pages.admin.celery.loadError", err);
  } finally {
    loading.value = false;
  }
}

function formatPayload(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (Array.isArray(value) && value.length === 0) return "-";
  if (typeof value === "object" && Object.keys(value || {}).length === 0)
    return "-";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function formatRuntime(runtime: number | null | undefined): string {
  if (runtime === null || runtime === undefined) return "-";
  return t("pages.admin.celery.runtimeSeconds", { runtime });
}

async function onTerminateSelected() {
  if (!selectedIds.value.size) return;

  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.celery.terminateConfirm", {
      count: selectedIds.value.size,
    }),
    danger: true,
  });
  if (!ok) return;

  terminating.value = true;
  try {
    const result = await terminateCeleryTasks({
      task_ids: [...selectedIds.value],
    });
    toastStore.push({
      type: "success",
      message: t("pages.admin.celery.terminated", {
        count: result.terminated,
      }),
    });
    await load();
  } catch (err) {
    showError("pages.admin.celery.terminateError", err);
  } finally {
    terminating.value = false;
  }
}

async function onTerminateTask(task: CeleryTaskInfo) {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.celery.terminateTaskConfirm", {
      task_id: task.task_id,
    }),
    danger: true,
  });
  if (!ok) return;

  terminating.value = true;
  try {
    const result = await terminateCeleryTasks({ task_ids: [task.task_id] });
    toastStore.push({
      type: "success",
      message: t("pages.admin.celery.terminated", {
        count: result.terminated,
      }),
    });
    await load();
  } catch (err) {
    showError("pages.admin.celery.terminateError", err);
  } finally {
    terminating.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <div class="celery-view">
    <AppPageTitle icon="gears">
      {{ t("pages.admin.celery.title") }}
    </AppPageTitle>

    <div class="celery-view__toolbar">
      <AppButton
        :loading="loading"
        :disabled="terminating"
        icon="rotate"
        @click="load"
      >
        {{ t("pages.admin.celery.refresh") }}
      </AppButton>

      <div class="celery-view__select-all">
        <AppCheckbox
          :model-value="allSelected"
          :indeterminate="someSelected"
          :label="t('pages.admin.celery.selectAll')"
          @update:model-value="toggleSelectAll"
        />
      </div>

      <AppButton
        v-if="selectedCount > 0"
        variant="danger"
        :loading="terminating"
        :disabled="loading"
        icon="stop"
        @click="onTerminateSelected"
      >
        {{
          t("pages.admin.celery.terminateSelected", { count: selectedCount })
        }}
      </AppButton>
    </div>

    <div v-if="error" class="celery-view__error" role="alert">
      <AppIcon name="circle-exclamation" />
      {{ error }}
    </div>

    <div v-if="loading && tasks.length === 0" class="celery-view__loading">
      <AppSpinner />
    </div>

    <ul v-else-if="tasks.length > 0" class="celery-view__cards" role="list">
      <li v-for="task in tasks" :key="task.task_id" class="celery-view__card">
        <div class="celery-view__card-header">
          <AppCheckbox
            :model-value="isSelected(task)"
            :aria-label="
              t('pages.admin.celery.selectTask', { task_id: task.task_id })
            "
            @update:model-value="(value: boolean) => toggleSelect(task, value)"
          />
          <span class="celery-view__card-name" :title="task.name">
            {{ task.name }}
          </span>
          <span class="celery-view__card-runtime">
            {{ formatRuntime(task.runtime) }}
          </span>
        </div>

        <dl class="celery-view__card-body">
          <div>
            <dt>{{ t("pages.admin.celery.taskId") }}</dt>
            <dd>
              <code class="celery-view__task-id">{{ task.task_id }}</code>
            </dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.celery.worker") }}</dt>
            <dd class="celery-view__task-worker">{{ task.worker }}</dd>
          </div>
          <div v-if="task.args && task.args.length">
            <dt>{{ t("pages.admin.celery.args") }}</dt>
            <dd>
              <code class="celery-view__card-payload">{{
                formatPayload(task.args)
              }}</code>
            </dd>
          </div>
          <div v-if="task.kwargs && Object.keys(task.kwargs).length">
            <dt>{{ t("pages.admin.celery.kwargs") }}</dt>
            <dd>
              <code class="celery-view__card-payload">{{
                formatPayload(task.kwargs)
              }}</code>
            </dd>
          </div>
        </dl>

        <div class="celery-view__card-footer">
          <AppButton
            variant="danger"
            size="sm"
            icon="stop"
            :loading="terminating"
            :disabled="loading"
            @click="onTerminateTask(task)"
          >
            {{ t("pages.admin.celery.terminate") }}
          </AppButton>
        </div>
      </li>
    </ul>

    <div v-else class="celery-view__empty" role="status">
      {{ t("pages.admin.celery.empty") }}
    </div>
  </div>
</template>

<style scoped>
.celery-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.celery-view__toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.celery-view__select-all {
  display: flex;
  align-items: center;
}

.celery-view__error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-danger-surface, var(--color-surface));
  color: var(--color-danger);
  border: 1px solid var(--color-danger);
}

.celery-view__loading,
.celery-view__empty {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.celery-view__cards {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.celery-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.celery-view__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 0;
}

.celery-view__card-name {
  flex: 1;
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.celery-view__card-runtime {
  flex: 0 0 auto;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.celery-view__card-body {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.celery-view__card-body div {
  display: grid;
  grid-template-columns: 5rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.celery-view__card-body dt {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}

.celery-view__card-body dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.celery-view__task-id {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono, monospace);
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.celery-view__task-worker {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  overflow-wrap: anywhere;
}

.celery-view__card-payload {
  display: block;
  max-width: 100%;
  font-family: var(--font-mono, monospace);
  font-size: 0.75rem;
  color: var(--color-text-muted);
  white-space: pre-wrap;
  word-break: break-word;
}

.celery-view__card-footer {
  display: flex;
  justify-content: flex-end;
}
</style>
