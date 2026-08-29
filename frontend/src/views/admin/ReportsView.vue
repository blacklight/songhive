<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { getApiErrorMessage } from "@/api/client";
import { listReports, updateReport, type ReportResponse } from "@/api/admin";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppTable from "@/components/ui/AppTable.vue";

const { t } = useI18n();
const toastStore = useToastStore();

const statusFilter = ref("");
const selectedReport = ref<ReportResponse | null>(null);
const resolutionNotes = ref("");
const isSubmitting = ref(false);

const { items, loading, error, hasMore, load, loadMore, refresh } =
  useEntityList<ReportResponse>(
    (params) =>
      listReports({
        status: statusFilter.value || undefined,
        limit: params.limit,
        offset: params.offset,
      }),
    { defaultLimit: 25 },
  );

const statusOptions = [
  { value: "", label: t("pages.admin.reports.allStatuses") },
  { value: "pending", label: t("pages.admin.reports.statuses.pending") },
  { value: "reviewed", label: t("pages.admin.reports.statuses.reviewed") },
  { value: "resolved", label: t("pages.admin.reports.statuses.resolved") },
  { value: "dismissed", label: t("pages.admin.reports.statuses.dismissed") },
];

const columns = [
  { key: "target_type", label: t("pages.admin.reports.targetType") },
  { key: "target_id", label: t("pages.admin.reports.targetId") },
  { key: "reason", label: t("pages.admin.reports.reason") },
  { key: "status", label: t("pages.admin.reports.status") },
  { key: "created_at", label: t("pages.admin.reports.createdAt") },
  {
    key: "actions",
    label: t("pages.admin.reports.details"),
    align: "right" as const,
  },
];

function reportFromRow(
  row: Record<string, unknown>,
): ReportResponse | undefined {
  return items.value.find((r) => r.id === row.id);
}

function openReport(report: ReportResponse) {
  selectedReport.value = report;
  resolutionNotes.value = report.resolution_notes || "";
}

function closeReport() {
  selectedReport.value = null;
  resolutionNotes.value = "";
}

async function onFilterChange() {
  await refresh();
}

async function submitReport(status: ReportResponse["status"]) {
  if (!selectedReport.value || isSubmitting.value) return;
  isSubmitting.value = true;
  try {
    await updateReport(selectedReport.value.id, {
      status,
      resolution_notes: resolutionNotes.value || null,
    });
    toastStore.push({
      type: "success",
      message:
        status === "resolved"
          ? t("pages.admin.reports.resolveSuccess")
          : t("pages.admin.reports.dismissSuccess"),
    });
    closeReport();
    await refresh();
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.reports.actionError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    isSubmitting.value = false;
  }
}

onMounted(() => load());
</script>

<template>
  <div class="reports-view">
    <header class="reports-view__header">
      <AppPageTitle icon="flag">{{
        t("pages.admin.reports.title")
      }}</AppPageTitle>
      <AppSelect
        v-model="statusFilter"
        :label="t('pages.admin.reports.filterStatus')"
        :options="statusOptions"
        @update:model-value="onFilterChange"
      />
    </header>

    <div v-if="error" class="reports-view__error" role="alert">
      {{ error }}
    </div>

    <AppTable
      :columns="columns"
      :rows="items as unknown as Record<string, unknown>[]"
      :row-key="(row) => String(row.id)"
      :loading="loading && items.length === 0"
      :empty-label="t('pages.admin.reports.empty')"
    >
      <template #row-created_at="{ row }">
        {{ formatDateTime((row as ReportResponse).created_at) }}
      </template>

      <template #row-actions="{ row }">
        <AppButton
          v-if="reportFromRow(row)"
          size="sm"
          variant="secondary"
          @click="openReport(reportFromRow(row)!)"
        >
          {{ t("pages.admin.reports.details") }}
        </AppButton>
      </template>
    </AppTable>

    <div v-if="hasMore" class="reports-view__load-more">
      <AppButton :loading="loading" :disabled="loading" @click="loadMore">
        {{ t("pages.admin.reports.loadMore") }}
      </AppButton>
    </div>

    <AppModal
      v-if="selectedReport"
      :open="!!selectedReport"
      :title="t('pages.admin.reports.details')"
      @close="closeReport"
    >
      <div class="reports-view__modal-body">
        <dl class="reports-view__details">
          <div>
            <dt>{{ t("pages.admin.reports.targetType") }}</dt>
            <dd>{{ selectedReport.target_type }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.reports.targetId") }}</dt>
            <dd>{{ selectedReport.target_id }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.reports.reason") }}</dt>
            <dd>{{ selectedReport.reason }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.reports.reporter") }}</dt>
            <dd>{{ selectedReport.reporter_id }}</dd>
          </div>
          <div v-if="selectedReport.description">
            <dt>{{ t("pages.admin.reports.description") }}</dt>
            <dd>{{ selectedReport.description }}</dd>
          </div>
        </dl>

        <AppInput
          v-model="resolutionNotes"
          as="textarea"
          :label="t('pages.admin.reports.resolutionNotes')"
          :rows="4"
        />
      </div>

      <template #actions>
        <AppButton variant="secondary" icon="xmark" @click="closeReport">
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          variant="secondary"
          :loading="isSubmitting"
          @click="submitReport('dismissed')"
        >
          {{ t("pages.admin.reports.dismiss") }}
        </AppButton>
        <AppButton
          :loading="isSubmitting"
          icon="check"
          @click="submitReport('resolved')"
        >
          {{ t("pages.admin.reports.resolve") }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.reports-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.reports-view__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.reports-view__error {
  color: var(--color-danger);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
}

.reports-view__load-more {
  display: flex;
  justify-content: center;
}

.reports-view__modal-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.reports-view__details {
  display: grid;
  gap: var(--space-3);
  margin: 0;
}

.reports-view__details div {
  display: grid;
  grid-template-columns: 8rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.reports-view__details dt {
  color: var(--color-text-muted);
  font-weight: 500;
}

.reports-view__details dd {
  margin: 0;
}
</style>
