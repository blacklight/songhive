<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  deleteShareGrant,
  listReceivedShares,
  type ReceivedShareResponse,
  type ShareItemType,
} from "@/api/shares";
import { getApiErrorMessage } from "@/api/client";
import { useEntityList } from "@/composables/useEntityList";
import { useMediaQuery } from "@/composables/useMediaQuery";
import { useConfirm } from "@/composables/useConfirm";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppTable, { type Column } from "@/components/ui/AppTable.vue";
import UserLink from "@/components/user/UserLink.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const { confirm } = useConfirm();
const isWide = useMediaQuery("(min-width: 1280px)", true);

const ITEM_TYPES: ShareItemType[] = [
  "track",
  "album",
  "artist",
  "playlist",
  "library",
];

const itemTypeFilter = ref<ShareItemType | "">("");

const itemTypeOptions = computed(() => [
  { value: "", label: t("pages.shares.received.allTypes") },
  ...ITEM_TYPES.map((type) => ({
    value: type,
    label: t(`browse.entities.${type}`),
  })),
]);

const {
  items,
  loading,
  loadingMore,
  error,
  hasMore,
  load,
  loadMore,
  retry,
  refresh,
} = useEntityList<ReceivedShareResponse>(
  (params) =>
    listReceivedShares({
      item_type: itemTypeFilter.value || undefined,
      limit: params.limit,
      offset: params.offset,
    }),
  { defaultLimit: 50 },
);

watch(itemTypeFilter, () => refresh());

const columns = computed<Column[]>(() => {
  const cols: Column[] = [];
  if (bulkMode.value) {
    cols.push({ key: "select", label: "", width: "2.75rem" });
  }
  cols.push(
    { key: "itemType", label: t("pages.shares.columns.type") },
    { key: "resource", label: t("pages.shares.columns.resource") },
    { key: "sharedBy", label: t("pages.shares.received.sharedBy") },
    { key: "role", label: t("browse.share.role") },
    { key: "createdAt", label: t("browse.share.createdAt") },
    { key: "actions", label: t("browse.detail.actions") },
  );
  return cols;
});

function resourceName(share: ReceivedShareResponse): string {
  return share.item_title ?? t("pages.shares.unknownItem");
}

function itemTypeLabel(share: ReceivedShareResponse): string {
  const key = `browse.entities.${share.item_type}`;
  const label = t(key);
  return label === key ? share.item_type : label;
}

function roleLabel(share: ReceivedShareResponse): string {
  return share.collaborator
    ? t("browse.share.roleCollaborator")
    : t("browse.share.roleViewer");
}

const bulkMode = ref(false);
const selectedIds = ref<Set<string>>(new Set());
const isLeaving = ref(false);

const allSelected = computed(
  () =>
    items.value.length > 0 &&
    items.value.every((s) => selectedIds.value.has(s.id)),
);
const someSelected = computed(
  () => selectedIds.value.size > 0 && !allSelected.value,
);

function toggleSelect(id: string) {
  if (selectedIds.value.has(id)) {
    selectedIds.value.delete(id);
  } else {
    selectedIds.value.add(id);
  }
}

function toggleAll() {
  if (allSelected.value) {
    selectedIds.value.clear();
  } else {
    for (const share of items.value) selectedIds.value.add(share.id);
  }
}

function exitBulkMode() {
  bulkMode.value = false;
  selectedIds.value.clear();
}

async function leaveOne(share: ReceivedShareResponse) {
  const confirmed = await confirm({
    title: t("browse.share.leave"),
    message: t("browse.share.leaveConfirm", { name: resourceName(share) }),
    danger: true,
    confirmLabel: t("browse.share.leave"),
  });
  if (!confirmed) return;

  isLeaving.value = true;
  try {
    await deleteShareGrant(share.id);
    toastStore.push({ type: "success", message: t("browse.share.left") });
    await refresh();
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("browse.share.leaveError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    isLeaving.value = false;
  }
}

async function leaveSelected() {
  const ids = [...selectedIds.value];
  if (ids.length === 0 || isLeaving.value) return;

  const confirmed = await confirm({
    title: t("browse.share.leave"),
    message: t("browse.share.leaveBulkConfirm", { count: ids.length }),
    danger: true,
    confirmLabel: t("browse.share.leave"),
  });
  if (!confirmed) return;

  isLeaving.value = true;
  let left = 0;
  try {
    for (const id of ids) {
      await deleteShareGrant(id);
      left++;
    }
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("browse.share.leaveError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    isLeaving.value = false;
  }
  if (left > 0) {
    toastStore.push({
      type: "success",
      message: t("browse.share.leftBulk", { count: left }),
    });
    selectedIds.value.clear();
    bulkMode.value = false;
    await refresh();
  }
}

onMounted(() => load());
</script>

<template>
  <div class="shares-panel">
    <div class="shares-view__actions">
      <AppSelect
        v-model="itemTypeFilter"
        :options="itemTypeOptions"
        :aria-label="t('pages.shares.received.filterType')"
      />
      <template v-if="!bulkMode">
        <AppButton
          v-if="items.length > 0 && !loading"
          size="sm"
          icon="pen-to-square"
          variant="secondary"
          @click="bulkMode = true"
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
          @update:model-value="toggleAll"
        />
        <AppButton
          variant="danger"
          size="sm"
          icon="right-from-bracket"
          :disabled="selectedIds.size === 0 || isLeaving"
          :loading="isLeaving"
          @click="leaveSelected"
        >
          {{ t("browse.share.leaveSelected") }}
        </AppButton>
        <AppButton
          size="sm"
          icon="xmark"
          variant="secondary"
          :disabled="isLeaving"
          @click="exitBulkMode"
        >
          {{ t("browse.bulkEdit.done") }}
        </AppButton>
      </template>
    </div>

    <div v-if="error" class="shares-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else>
      <div v-if="loading && items.length === 0" class="shares-view__loading">
        <AppSpinner />
      </div>

      <template v-else-if="items.length > 0">
        <AppTable
          v-if="isWide"
          :columns="columns"
          :rows="items"
          :row-key="(row) => String(row.id)"
          :empty-label="t('pages.shares.received.empty')"
        >
          <template #column-select>
            <AppCheckbox
              :model-value="allSelected"
              :indeterminate="someSelected"
              :aria-label="t('browse.bulkEdit.selectAll')"
              @update:model-value="toggleAll"
            />
          </template>

          <template #row-select="{ row }">
            <AppCheckbox
              :model-value="selectedIds.has(String(row.id))"
              :aria-label="t('browse.bulkEdit.selectAll')"
              @update:model-value="toggleSelect(String(row.id))"
            />
          </template>

          <template #row-itemType="{ row }">
            {{ itemTypeLabel(row as ReceivedShareResponse) }}
          </template>

          <template #row-resource="{ row }">
            <RouterLink
              v-if="(row as ReceivedShareResponse).item_url"
              :to="(row as ReceivedShareResponse).item_url!"
              class="shares-view__resource"
            >
              {{ resourceName(row as ReceivedShareResponse) }}
            </RouterLink>
            <span v-else>{{ resourceName(row as ReceivedShareResponse) }}</span>
          </template>

          <template #row-sharedBy="{ row }">
            <UserLink
              v-if="(row as ReceivedShareResponse).shared_by"
              :owner="(row as ReceivedShareResponse).shared_by"
              size="sm"
            />
            <span v-else>—</span>
          </template>

          <template #row-role="{ row }">
            {{ roleLabel(row as ReceivedShareResponse) }}
          </template>

          <template #row-createdAt="{ row }">
            {{ formatDateTime((row as ReceivedShareResponse).created_at) }}
          </template>

          <template #row-actions="{ row }">
            <AppButton
              size="sm"
              variant="danger"
              icon="right-from-bracket"
              :disabled="isLeaving"
              @click="leaveOne(row as ReceivedShareResponse)"
            >
              {{ t("browse.share.leave") }}
            </AppButton>
          </template>
        </AppTable>

        <ul v-else class="shares-view__cards" role="list">
          <li v-for="share in items" :key="share.id" class="shares-view__card">
            <div class="shares-view__card-header">
              <AppCheckbox
                v-if="bulkMode"
                :model-value="selectedIds.has(share.id)"
                :aria-label="t('browse.bulkEdit.selectAll')"
                @update:model-value="toggleSelect(share.id)"
              />
              <span class="shares-view__card-type">
                {{ itemTypeLabel(share) }}
              </span>
              <span class="shares-view__card-status">
                {{ roleLabel(share) }}
              </span>
            </div>

            <dl class="shares-view__card-body">
              <div>
                <dt>{{ t("pages.shares.columns.resource") }}</dt>
                <dd>
                  <RouterLink
                    v-if="share.item_url"
                    :to="share.item_url"
                    class="shares-view__resource"
                  >
                    {{ resourceName(share) }}
                  </RouterLink>
                  <template v-else>{{ resourceName(share) }}</template>
                </dd>
              </div>
              <div>
                <dt>{{ t("pages.shares.received.sharedBy") }}</dt>
                <dd>
                  <UserLink
                    v-if="share.shared_by"
                    :owner="share.shared_by"
                    size="sm"
                  />
                  <template v-else>—</template>
                </dd>
              </div>
              <div>
                <dt>{{ t("browse.share.createdAt") }}</dt>
                <dd>
                  <time :datetime="share.created_at">
                    {{ formatDateTime(share.created_at) }}
                  </time>
                </dd>
              </div>
            </dl>

            <div class="shares-view__card-footer">
              <AppButton
                size="sm"
                variant="danger"
                icon="right-from-bracket"
                :disabled="isLeaving"
                @click="leaveOne(share)"
              >
                {{ t("browse.share.leave") }}
              </AppButton>
            </div>
          </li>
        </ul>
      </template>

      <div v-else class="shares-view__empty" role="status">
        {{ t("pages.shares.received.empty") }}
      </div>
    </template>

    <div class="shares-view__footer">
      <AppButton
        v-if="hasMore"
        variant="secondary"
        :loading="loading || loadingMore"
        :disabled="loading || loadingMore"
        icon="chevron-down"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.shares-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.shares-view__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  justify-content: flex-end;
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
