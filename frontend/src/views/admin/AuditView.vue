<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, type RouteLocationRaw } from "vue-router";
import { useEntityList } from "@/composables/useEntityList";
import { useMediaQuery } from "@/composables/useMediaQuery";
import { useDebounce } from "@/composables/useDebounce";
import { formatDateTime } from "@/i18n";
import { listAuditLogs, type AuditLogResponse } from "@/api/admin";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppTable from "@/components/ui/AppTable.vue";

const { t } = useI18n();
const isWide = useMediaQuery("(min-width: 1280px)", true);

const actionFilter = ref("");
const targetTypeFilter = ref("");
const selectedLog = ref<AuditLogResponse | null>(null);

const TARGET_ROUTE_NAMES: Record<string, string> = {
  album: "album",
  artist: "artist",
  file: "file",
  library: "library",
  playlist: "playlist",
  track: "track",
};

const targetTypeOptions = [
  { value: "", label: t("pages.admin.audit.allTargetTypes") },
  { value: "user", label: t("pages.admin.audit.targetTypes.user") },
  { value: "track", label: t("pages.admin.audit.targetTypes.track") },
  { value: "playlist", label: t("pages.admin.audit.targetTypes.playlist") },
  { value: "album", label: t("pages.admin.audit.targetTypes.album") },
  { value: "artist", label: t("pages.admin.audit.targetTypes.artist") },
  { value: "library", label: t("pages.admin.audit.targetTypes.library") },
  { value: "hashtag", label: t("pages.admin.audit.targetTypes.hashtag") },
  { value: "report", label: t("pages.admin.audit.targetTypes.report") },
  { value: "file", label: t("pages.admin.audit.targetTypes.file") },
  { value: "invite", label: t("pages.admin.audit.targetTypes.invite") },
  {
    value: "oauth_client",
    label: t("pages.admin.audit.targetTypes.oauth_client"),
  },
];

const { items, loading, error, hasMore, load, loadMore, refresh } =
  useEntityList<AuditLogResponse>(
    (params) =>
      listAuditLogs({
        action: actionFilter.value || undefined,
        target_type: targetTypeFilter.value || undefined,
        limit: params.limit,
        offset: params.offset,
      }),
    { defaultLimit: 25 },
  );

const columns = [
  {
    key: "created_at",
    label: t("pages.admin.audit.timestamp"),
    width: "11rem",
  },
  {
    key: "actor",
    label: t("pages.admin.audit.actor"),
    width: "12rem",
  },
  {
    key: "action",
    label: t("pages.admin.audit.action"),
    width: "10rem",
  },
  {
    key: "target",
    label: t("pages.admin.audit.target"),
    width: "22rem",
  },
  {
    key: "actions",
    label: t("pages.admin.audit.details"),
    align: "right" as const,
    width: "6rem",
  },
];

const debouncedRefresh = useDebounce(() => refresh(), 300);

function targetTypeLabel(type: string | null): string {
  if (!type) return "—";
  const key = `pages.admin.audit.targetTypes.${type}`;
  const translated = t(key);
  return translated !== key ? translated : type;
}

function actorDisplayName(log: AuditLogResponse): string {
  return log.actor_name || log.actor_username || log.actor_id || "—";
}

function targetDisplayName(log: AuditLogResponse): string {
  return log.target_name || log.target_username || log.target_id || "—";
}

function targetRoute(log: AuditLogResponse): RouteLocationRaw {
  if (!log.target_id || !log.target_type) return "";
  const routeName = TARGET_ROUTE_NAMES[log.target_type];
  if (!routeName) return "";
  return { name: routeName, params: { id: log.target_id } };
}

function logFromRow(
  row: Record<string, unknown>,
): AuditLogResponse | undefined {
  return items.value.find((l) => l.id === row.id);
}

function openDetails(log: AuditLogResponse) {
  selectedLog.value = log;
}

function closeDetails() {
  selectedLog.value = null;
}

function formatDetails(details: unknown): string {
  if (details === null || details === undefined) return "—";
  return JSON.stringify(details, null, 2);
}

watch(actionFilter, () => debouncedRefresh());

async function onTargetTypeChange() {
  await refresh();
}

onMounted(() => load());
</script>

<template>
  <div class="audit-view">
    <header class="audit-view__header">
      <AppPageTitle icon="clipboard-list">
        {{ t("pages.admin.audit.title") }}
      </AppPageTitle>
      <div class="audit-view__filters">
        <AppInput
          v-model="actionFilter"
          type="search"
          :label="t('pages.admin.audit.filterAction')"
        />
        <AppSelect
          v-model="targetTypeFilter"
          :label="t('pages.admin.audit.filterTargetType')"
          :options="targetTypeOptions"
          @update:model-value="onTargetTypeChange"
        />
      </div>
    </header>

    <div v-if="error" class="audit-view__error" role="alert">
      {{ error }}
    </div>

    <div v-if="loading && items.length === 0" class="audit-view__loading">
      <AppSpinner />
    </div>

    <template v-else-if="items.length > 0">
      <AppTable
        v-if="isWide"
        :columns="columns"
        :rows="items as unknown as Record<string, unknown>[]"
        :row-key="(row) => String(row.id)"
        :loading="loading && items.length === 0"
        :empty-label="t('pages.admin.audit.empty')"
        class="audit-view__table"
      >
        <template #row-created_at="{ row }">
          <time
            :datetime="(row as AuditLogResponse).created_at"
            class="audit-view__timestamp"
          >
            {{ formatDateTime((row as AuditLogResponse).created_at) }}
          </time>
        </template>

        <template #row-actor="{ row }">
          <span
            :title="(row as AuditLogResponse).actor_id || undefined"
            class="audit-view__actor"
          >
            {{ actorDisplayName(row as AuditLogResponse) }}
          </span>
        </template>

        <template #row-action="{ row }">
          <span class="audit-view__action">{{
            (row as AuditLogResponse).action
          }}</span>
        </template>

        <template #row-target="{ row }">
          <RouterLink
            v-if="targetRoute(row as AuditLogResponse)"
            :to="targetRoute(row as AuditLogResponse)"
            class="audit-view__target-link"
            @click.stop
          >
            {{ targetDisplayName(row as AuditLogResponse) }}
          </RouterLink>
          <span
            v-else
            :title="(row as AuditLogResponse).target_id || undefined"
            class="audit-view__target-text"
          >
            {{ targetDisplayName(row as AuditLogResponse) }}
          </span>
        </template>

        <template #row-actions="{ row }">
          <AppButton
            v-if="logFromRow(row)"
            size="sm"
            variant="secondary"
            @click="openDetails(logFromRow(row)!)"
          >
            {{ t("pages.admin.audit.details") }}
          </AppButton>
        </template>
      </AppTable>

      <ul v-else class="audit-view__cards" role="list">
        <li v-for="log in items" :key="log.id" class="audit-view__card">
          <div class="audit-view__card-header">
            <span class="audit-view__card-action">{{ log.action }}</span>
            <span class="audit-view__card-type">
              {{ targetTypeLabel(log.target_type) }}
            </span>
          </div>

          <dl class="audit-view__card-body">
            <div>
              <dt>{{ t("pages.admin.audit.timestamp") }}</dt>
              <dd>
                <time :datetime="log.created_at">
                  {{ formatDateTime(log.created_at) }}
                </time>
              </dd>
            </div>
            <div>
              <dt>{{ t("pages.admin.audit.actor") }}</dt>
              <dd :title="log.actor_id || undefined">
                {{ actorDisplayName(log) }}
              </dd>
            </div>
            <div v-if="log.target_type">
              <dt>{{ t("pages.admin.audit.target") }}</dt>
              <dd :title="log.target_id || undefined">
                <RouterLink
                  v-if="targetRoute(log)"
                  :to="targetRoute(log)"
                  class="audit-view__target-link"
                >
                  {{ targetDisplayName(log) }}
                </RouterLink>
                <template v-else>
                  {{ targetDisplayName(log) }}
                </template>
              </dd>
            </div>
          </dl>

          <div class="audit-view__card-footer">
            <AppButton size="sm" variant="secondary" @click="openDetails(log)">
              {{ t("pages.admin.audit.details") }}
            </AppButton>
          </div>
        </li>
      </ul>
    </template>

    <div v-else class="audit-view__empty" role="status">
      {{ t("pages.admin.audit.empty") }}
    </div>

    <div v-if="hasMore" class="audit-view__load-more">
      <AppButton :loading="loading" :disabled="loading" @click="loadMore">
        {{ t("pages.admin.audit.loadMore") }}
      </AppButton>
    </div>

    <AppModal
      v-if="selectedLog"
      :open="!!selectedLog"
      :title="t('pages.admin.audit.details')"
      @close="closeDetails"
    >
      <pre v-if="selectedLog.details" class="audit-view__json">{{
        formatDetails(selectedLog.details)
      }}</pre>
      <p v-else>{{ t("pages.admin.audit.empty") }}</p>

      <template #actions>
        <AppButton variant="secondary" icon="xmark" @click="closeDetails">
          {{ t("common.close") }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.audit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.audit-view__header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.audit-view__filters {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.audit-view__filters > * {
  min-width: 12rem;
}

.audit-view__error {
  color: var(--color-danger);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
}

.audit-view__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.audit-view__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.audit-view__table :deep(.app-table) {
  table-layout: fixed;
  width: 100%;
}

.audit-view__table :deep(.app-table__cell--created_at) {
  font-variant-numeric: tabular-nums;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  white-space: nowrap;
}

.audit-view__table :deep(.app-table__cell--actor) {
  font-weight: 500;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.audit-view__table :deep(.app-table__cell--target) {
  font-size: 0.875rem;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.audit-view__table :deep(.app-table__cell--actions) {
  text-align: right;
  white-space: nowrap;
}

.audit-view__timestamp {
  font-variant-numeric: tabular-nums;
}

.audit-view__action {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.audit-view__target-type {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.audit-view__target-link,
.audit-view__target-text {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-view__target-link {
  color: var(--color-info);
  text-decoration: none;
}

.audit-view__target-link:hover {
  text-decoration: underline;
}

.audit-view__cards {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.audit-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.audit-view__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 0;
}

.audit-view__card-action {
  font-weight: 600;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.audit-view__card-type {
  flex: 0 0 auto;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.audit-view__card-body {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.audit-view__card-body div {
  display: grid;
  grid-template-columns: 7rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.audit-view__card-body dt {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}

.audit-view__card-body dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.audit-view__card-footer {
  display: flex;
  justify-content: flex-end;
}

.audit-view__load-more {
  display: flex;
  justify-content: center;
}

.audit-view__json {
  background-color: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: monospace;
  font-size: 0.875rem;
}
</style>
