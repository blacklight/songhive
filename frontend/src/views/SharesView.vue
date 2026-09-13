<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  deleteShareGrant,
  deleteShareUrl,
  listMyShares,
  type CreatedShareResponse,
} from "@/api/shares";
import { useAuthStore } from "@/stores/auth";
import { useEntityList } from "@/composables/useEntityList";
import { useMediaQuery } from "@/composables/useMediaQuery";
import {
  useBulkDelete,
  type ManageableItem,
} from "@/composables/useBulkDelete";
import { formatDateTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppTable, { type Column } from "@/components/ui/AppTable.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";

type ShareRow = ManageableItem &
  Record<string, unknown> & { share: CreatedShareResponse };

const { t } = useI18n();
const authStore = useAuthStore();
const isWide = useMediaQuery("(min-width: 1280px)", true);

const includeRevoked = ref(false);

const { items, loading, error, hasMore, load, loadMore, retry, refresh } =
  useEntityList<CreatedShareResponse>(
    (params) =>
      listMyShares({
        limit: params.limit,
        offset: params.offset,
        include_revoked: includeRevoked.value || undefined,
      }),
    { defaultLimit: 50 },
  );

watch(includeRevoked, () => refresh());

function toRow(share: CreatedShareResponse): ShareRow {
  return {
    // Composite ids keep grant and token ids distinct for bulk selection.
    id: `${share.kind}:${share.id}`,
    // Revoked links are inert: keep them visible but not removable.
    owner_id: share.revoked_at ? null : (authStore.user?.id ?? null),
    share,
  };
}

const rows = computed<ShareRow[]>(() => items.value.map(toRow));

function shareOf(row: Record<string, unknown>): CreatedShareResponse {
  return row.share as CreatedShareResponse;
}

function canManageRow(row: Record<string, unknown>): boolean {
  return bulk.canManage(row as ShareRow);
}

async function deleteOne(id: string): Promise<void> {
  const sep = id.indexOf(":");
  const kind = id.slice(0, sep);
  const rawId = id.slice(sep + 1);
  if (kind === "grant") {
    await deleteShareGrant(rawId);
  } else {
    await deleteShareUrl(rawId);
  }
}

const bulk = useBulkDelete<ShareRow>({
  deleteOne: (id) => deleteOne(id),
  refresh,
  entitySingular: t("browse.entities.share"),
  entityPlural: t("browse.entities.shares"),
  getName: (row) => resourceName(row.share),
});

const {
  bulkMode,
  selectedIds,
  isDeleting,
  deleteModalOpen,
  deleteModalTitle,
  deleteModalMessage,
  deleteModalAllowRecursive,
  deleteModalLoading,
} = bulk;

const hasManageable = computed(() =>
  rows.value.some((row) => bulk.canManage(row)),
);
const allSelected = computed(() => bulk.allSelected(rows.value));
const someSelected = computed(() => bulk.someSelected(rows.value));

const columns = computed<Column[]>(() => {
  const cols: Column[] = [];
  if (bulkMode.value) {
    cols.push({ key: "select", label: "", width: "2.75rem" });
  }
  cols.push(
    { key: "type", label: t("pages.shares.columns.type") },
    { key: "resource", label: t("pages.shares.columns.resource") },
    { key: "sharedWith", label: t("pages.shares.columns.sharedWith") },
    { key: "createdAt", label: t("browse.share.createdAt") },
    { key: "status", label: t("pages.shares.columns.status") },
    { key: "actions", label: t("browse.detail.actions") },
  );
  return cols;
});

function resourceName(share: CreatedShareResponse): string {
  return share.item_title ?? t("pages.shares.unknownItem");
}

function typeIcon(share: CreatedShareResponse): string {
  return share.kind === "grant" ? "users" : "link";
}

function typeLabel(share: CreatedShareResponse): string {
  return t(`pages.shares.types.${share.kind}`);
}

function itemTypeLabel(share: CreatedShareResponse): string {
  const key = `browse.entities.${share.item_type}`;
  const label = t(key);
  return label === key ? share.item_type : label;
}

function isExpired(share: CreatedShareResponse): boolean {
  return (
    share.kind === "url" &&
    !share.revoked_at &&
    !!share.expires_at &&
    new Date(share.expires_at).getTime() <= Date.now()
  );
}

function statusLabel(share: CreatedShareResponse): string {
  if (share.kind === "url") {
    if (share.revoked_at) return t("pages.shares.status.revoked");
    if (isExpired(share)) return t("pages.shares.status.expired");
  }
  return t("pages.shares.status.active");
}

onMounted(() => load());
</script>

<template>
  <div class="shares-view">
    <div class="shares-view__header">
      <AppPageTitle icon="share-nodes">{{
        t("pages.shares.title")
      }}</AppPageTitle>
      <div class="shares-view__actions">
        <AppCheckbox
          v-model="includeRevoked"
          :label="t('pages.shares.includeRevoked')"
        />
        <template v-if="!bulkMode">
          <AppButton
            v-if="hasManageable && !loading"
            size="sm"
            icon="pen-to-square"
            variant="secondary"
            @click="bulk.enterBulkMode"
          >
            {{ t("browse.bulkEdit.start") }}
          </AppButton>
        </template>
        <template v-else>
          <AppCheckbox
            v-if="!isWide"
            :model-value="allSelected"
            :indeterminate="someSelected"
            :aria-label="t('browse.bulkEdit.selectAll')"
            @update:model-value="bulk.toggleAll(rows)"
          />
          <AppButton
            variant="danger"
            size="sm"
            icon="trash"
            :disabled="selectedIds.size === 0 || isDeleting"
            :loading="isDeleting"
            @click="bulk.openDeleteBulk(rows)"
          >
            {{ t("browse.bulkEdit.deleteSelected") }}
          </AppButton>
          <AppButton
            size="sm"
            icon="xmark"
            variant="secondary"
            :disabled="isDeleting"
            @click="bulk.exitBulkMode"
          >
            {{ t("browse.bulkEdit.done") }}
          </AppButton>
        </template>
      </div>
    </div>

    <div v-if="error" class="shares-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else>
      <div v-if="loading && rows.length === 0" class="shares-view__loading">
        <AppSpinner />
      </div>

      <template v-else-if="rows.length > 0">
        <AppTable
          v-if="isWide"
          :columns="columns"
          :rows="rows"
          :row-key="(row) => String(row.id)"
          :empty-label="t('pages.shares.empty')"
        >
          <template #column-select>
            <AppCheckbox
              :model-value="allSelected"
              :indeterminate="someSelected"
              :aria-label="t('browse.bulkEdit.selectAll')"
              @update:model-value="bulk.toggleAll(rows)"
            />
          </template>

          <template #row-select="{ row }">
            <AppCheckbox
              :model-value="selectedIds.has(String(row.id))"
              :disabled="!canManageRow(row)"
              :aria-label="t('browse.bulkEdit.selectAll')"
              @update:model-value="bulk.toggleSelect(String(row.id))"
            />
          </template>

          <template #row-type="{ row }">
            <AppIcon
              :name="typeIcon(shareOf(row))"
              spacing="right"
              :title="typeLabel(shareOf(row))"
            />
            {{ typeLabel(shareOf(row)) }}
          </template>

          <template #row-resource="{ row }">
            <RouterLink
              v-if="shareOf(row).item_url"
              :to="shareOf(row).item_url!"
              class="shares-view__resource"
            >
              {{ resourceName(shareOf(row)) }}
            </RouterLink>
            <span v-else>{{ resourceName(shareOf(row)) }}</span>
            <div class="shares-view__item-type">
              {{ itemTypeLabel(shareOf(row)) }}
            </div>
          </template>

          <template #row-sharedWith="{ row }">
            <RouterLink
              v-if="shareOf(row).username"
              :to="`/@${shareOf(row).username}`"
            >
              {{ shareOf(row).username }}
            </RouterLink>
            <span v-else-if="shareOf(row).kind === 'url'">
              {{ t("pages.shares.anyoneWithLink") }}
            </span>
            <span v-else>{{ shareOf(row).user_id }}</span>
          </template>

          <template #row-createdAt="{ row }">
            {{ formatDateTime(shareOf(row).created_at) }}
          </template>

          <template #row-status="{ row }">
            <span>{{ statusLabel(shareOf(row)) }}</span>
            <div
              v-if="shareOf(row).expires_at && !shareOf(row).revoked_at"
              class="shares-view__expires"
            >
              {{
                t("pages.shares.expiresAt", {
                  date: formatDateTime(shareOf(row).expires_at),
                })
              }}
            </div>
          </template>

          <template #row-actions="{ row }">
            <AppButton
              v-if="canManageRow(row)"
              size="sm"
              variant="danger"
              icon="trash-can"
              @click="bulk.openDeleteSingle(row as ShareRow)"
            >
              {{ t("browse.share.revoke") }}
            </AppButton>
          </template>
        </AppTable>

        <ul v-else class="shares-view__cards" role="list">
          <li
            v-for="row in rows"
            :key="String(row.id)"
            class="shares-view__card"
          >
            <div class="shares-view__card-header">
              <AppCheckbox
                v-if="bulkMode"
                :model-value="selectedIds.has(String(row.id))"
                :disabled="!canManageRow(row)"
                :aria-label="t('browse.bulkEdit.selectAll')"
                @update:model-value="bulk.toggleSelect(String(row.id))"
              />
              <span class="shares-view__card-type">
                <AppIcon :name="typeIcon(row.share)" spacing="right" />
                {{ typeLabel(row.share) }}
              </span>
              <span class="shares-view__card-status">
                {{ statusLabel(row.share) }}
              </span>
            </div>

            <dl class="shares-view__card-body">
              <div>
                <dt>{{ t("pages.shares.columns.resource") }}</dt>
                <dd>
                  <RouterLink
                    v-if="row.share.item_url"
                    :to="row.share.item_url"
                    class="shares-view__resource"
                  >
                    {{ resourceName(row.share) }}
                  </RouterLink>
                  <template v-else>{{ resourceName(row.share) }}</template>
                  <span class="shares-view__item-type">
                    · {{ itemTypeLabel(row.share) }}
                  </span>
                </dd>
              </div>
              <div>
                <dt>{{ t("pages.shares.columns.sharedWith") }}</dt>
                <dd>
                  <RouterLink
                    v-if="row.share.username"
                    :to="`/@${row.share.username}`"
                  >
                    {{ row.share.username }}
                  </RouterLink>
                  <template v-else-if="row.share.kind === 'url'">
                    {{ t("pages.shares.anyoneWithLink") }}
                  </template>
                  <template v-else>{{ row.share.user_id }}</template>
                </dd>
              </div>
              <div>
                <dt>{{ t("browse.share.createdAt") }}</dt>
                <dd>
                  <time :datetime="row.share.created_at">
                    {{ formatDateTime(row.share.created_at) }}
                  </time>
                </dd>
              </div>
              <div v-if="row.share.expires_at && !row.share.revoked_at">
                <dt>{{ t("pages.shares.columns.status") }}</dt>
                <dd>
                  {{
                    t("pages.shares.expiresAt", {
                      date: formatDateTime(row.share.expires_at),
                    })
                  }}
                </dd>
              </div>
            </dl>

            <div v-if="canManageRow(row)" class="shares-view__card-footer">
              <AppButton
                size="sm"
                variant="danger"
                icon="trash-can"
                @click="bulk.openDeleteSingle(row)"
              >
                {{ t("browse.share.revoke") }}
              </AppButton>
            </div>
          </li>
        </ul>
      </template>

      <div v-else class="shares-view__empty" role="status">
        {{ t("pages.shares.empty") }}
      </div>
    </template>

    <div class="shares-view__footer">
      <AppButton
        v-if="hasMore"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        icon="chevron-down"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
    </div>

    <DeleteModal
      :open="deleteModalOpen"
      :title="deleteModalTitle"
      :message="deleteModalMessage"
      :allow-recursive="deleteModalAllowRecursive"
      :loading="deleteModalLoading"
      @close="bulk.closeDeleteModal"
      @confirm="bulk.confirmDelete"
    />
  </div>
</template>

<style scoped>
.shares-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.shares-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.shares-view__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}

@media (max-width: 767px) {
  .shares-view__header {
    flex-direction: column;
    align-items: flex-start;
  }
}

.shares-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.shares-view__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.shares-view__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.shares-view__resource {
  color: var(--color-text);
}

.shares-view__item-type,
.shares-view__expires {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.shares-view__cards {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.shares-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.shares-view__card-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.shares-view__card-type {
  flex: 1;
  min-width: 0;
  font-weight: 500;
  color: var(--color-text);
}

.shares-view__card-status {
  flex: 0 0 auto;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
}

.shares-view__card-body {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.shares-view__card-body > div {
  display: grid;
  grid-template-columns: 7rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.shares-view__card-body dt {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}

.shares-view__card-body dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.shares-view__card-footer {
  display: flex;
  justify-content: flex-end;
}

.shares-view__footer {
  display: flex;
  justify-content: center;
}
</style>
