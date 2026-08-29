<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { useConfirm } from "@/composables/useConfirm";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import {
  listUsers,
  promoteUser,
  demoteUser,
  activateUser,
  deactivateUser,
  deleteUser,
  bulkUserAction,
  type AdminUserResponse,
  type BulkUserActionRequest,
} from "@/api/admin";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppTable from "@/components/ui/AppTable.vue";
import SearchBar from "@/components/ui/SearchBar.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const { confirm } = useConfirm();

const {
  items,
  loading,
  error,
  query,
  hasMore,
  load,
  loadMore,
  search,
  refresh,
} = useEntityList<AdminUserResponse>(
  (params) =>
    listUsers({
      q: params.q,
      limit: params.limit,
      offset: params.offset,
    }),
  { defaultLimit: 25 },
);

const selectedUserIds = ref<Set<string>>(new Set());
const isBulkLoading = ref(false);

const columns = computed(() => [
  { key: "select", label: "", width: "3rem" },
  { key: "username", label: t("pages.admin.users.username") },
  { key: "email", label: t("pages.admin.users.email") },
  { key: "role", label: t("pages.admin.users.role") },
  { key: "status", label: t("pages.admin.users.status") },
  {
    key: "actions",
    label: t("pages.admin.users.actions"),
    align: "right" as const,
  },
]);

const allSelected = computed(
  () =>
    items.value.length > 0 && selectedUserIds.value.size === items.value.length,
);

const someSelected = computed(
  () =>
    selectedUserIds.value.size > 0 &&
    selectedUserIds.value.size < items.value.length,
);

const selectedCount = computed(() => selectedUserIds.value.size);

function userFromRow(
  row: Record<string, unknown>,
): AdminUserResponse | undefined {
  return items.value.find((u) => u.id === row.id);
}

function isSelected(user: AdminUserResponse): boolean {
  return selectedUserIds.value.has(user.id);
}

function toggleSelect(user: AdminUserResponse, value: boolean) {
  if (value) {
    selectedUserIds.value.add(user.id);
  } else {
    selectedUserIds.value.delete(user.id);
  }
  selectedUserIds.value = new Set(selectedUserIds.value);
}

function toggleSelectAll(value: boolean) {
  if (value) {
    selectedUserIds.value = new Set(items.value.map((u) => u.id));
  } else {
    selectedUserIds.value.clear();
    selectedUserIds.value = new Set();
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

async function onPromote(user: AdminUserResponse) {
  try {
    await promoteUser(user.id);
    toastStore.push({
      type: "success",
      message: t("pages.admin.users.promoteSuccess"),
    });
    await refresh();
  } catch (err) {
    showError("pages.admin.users.actionError", err);
  }
}

async function onDemote(user: AdminUserResponse) {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.users.demote"),
  });
  if (!ok) return;

  try {
    await demoteUser(user.id);
    toastStore.push({
      type: "success",
      message: t("pages.admin.users.demoteSuccess"),
    });
    await refresh();
  } catch (err) {
    showError("pages.admin.users.actionError", err);
  }
}

async function onActivate(user: AdminUserResponse) {
  try {
    await activateUser(user.id);
    toastStore.push({
      type: "success",
      message: t("pages.admin.users.activateSuccess"),
    });
    await refresh();
  } catch (err) {
    showError("pages.admin.users.actionError", err);
  }
}

async function onDeactivate(user: AdminUserResponse) {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.users.deactivate"),
  });
  if (!ok) return;

  try {
    await deactivateUser(user.id);
    toastStore.push({
      type: "success",
      message: t("pages.admin.users.deactivateSuccess"),
    });
    await refresh();
  } catch (err) {
    showError("pages.admin.users.actionError", err);
  }
}

async function onDelete(user: AdminUserResponse) {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.users.deleteConfirm", { username: user.username }),
    danger: true,
  });
  if (!ok) return;

  try {
    await deleteUser(user.id);
    toastStore.push({
      type: "success",
      message: t("pages.admin.users.deleteSuccess"),
    });
    await refresh();
  } catch (err) {
    showError("pages.admin.users.actionError", err);
  }
}

async function runBulkAction(action: BulkUserActionRequest["action"]) {
  const userIds = [...selectedUserIds.value];
  if (userIds.length === 0) return;

  const isDelete = action === "delete";
  const ok = await confirm({
    title: t("common.confirm"),
    message: isDelete
      ? t("pages.admin.users.bulkDeleteConfirm", { count: userIds.length })
      : t("pages.admin.users.bulkActionConfirm", {
          action: t(`pages.admin.users.${action}`).toLowerCase(),
          count: userIds.length,
        }),
    danger: isDelete,
  });
  if (!ok) return;

  isBulkLoading.value = true;
  try {
    const result = await bulkUserAction({
      action,
      user_ids: userIds,
      recursive: isDelete,
    });
    toastStore.push({
      type: "success",
      message: t("pages.admin.users.bulkSuccess", {
        action: t(`pages.admin.users.${action}`),
        count: result.processed,
      }),
    });
    selectedUserIds.value.clear();
    selectedUserIds.value = new Set();
    await refresh();
  } catch (err) {
    showError("pages.admin.users.actionError", err);
  } finally {
    isBulkLoading.value = false;
  }
}

onMounted(() => load());
</script>

<template>
  <div class="users-view">
    <header class="users-view__header">
      <AppPageTitle icon="users">{{
        t("pages.admin.users.title")
      }}</AppPageTitle>
      <SearchBar
        :model-value="query"
        :debounce="0"
        :placeholder="t('pages.admin.users.searchPlaceholder')"
        @update:model-value="search"
      />
    </header>

    <div class="users-view__bulk-bar">
      <AppCheckbox
        :model-value="allSelected"
        :indeterminate="someSelected"
        :disabled="items.length === 0"
        @update:model-value="toggleSelectAll"
      >
        {{ t("pages.admin.users.selected", { count: selectedCount }) }}
      </AppCheckbox>
      <div class="users-view__bulk-actions">
        <AppButton
          size="sm"
          variant="secondary"
          :disabled="selectedCount === 0 || isBulkLoading"
          @click="runBulkAction('deactivate')"
        >
          {{ t("pages.admin.users.bulkDeactivate") }}
        </AppButton>
        <AppButton
          size="sm"
          variant="secondary"
          :disabled="selectedCount === 0 || isBulkLoading"
          @click="runBulkAction('activate')"
        >
          {{ t("pages.admin.users.bulkActivate") }}
        </AppButton>
        <AppButton
          size="sm"
          variant="danger"
          :disabled="selectedCount === 0 || isBulkLoading"
          @click="runBulkAction('delete')"
        >
          {{ t("pages.admin.users.bulkDelete") }}
        </AppButton>
      </div>
    </div>

    <div v-if="error" class="users-view__error" role="alert">
      {{ error }}
    </div>

    <AppTable
      :columns="columns"
      :rows="items as unknown as Record<string, unknown>[]"
      :row-key="(row) => String(row.id)"
      :loading="loading && items.length === 0"
      :empty-label="
        query
          ? t('pages.admin.users.emptySearch')
          : t('pages.admin.users.empty')
      "
    >
      <template #column-select>
        <AppCheckbox
          :model-value="allSelected"
          :indeterminate="someSelected"
          :disabled="items.length === 0"
          @update:model-value="toggleSelectAll"
        />
      </template>

      <template #row-select="{ row }">
        <AppCheckbox
          v-if="userFromRow(row)"
          :model-value="isSelected(userFromRow(row)!)"
          @update:model-value="
            toggleSelect(userFromRow(row)!, $event as boolean)
          "
        />
      </template>

      <template #row-status="{ row }">
        <span
          :class="[
            'users-view__status',
            userFromRow(row)?.is_active
              ? 'users-view__status--active'
              : 'users-view__status--inactive',
          ]"
        >
          {{
            userFromRow(row)?.is_active
              ? t("pages.admin.users.active")
              : t("pages.admin.users.inactive")
          }}
        </span>
      </template>

      <template #row-actions="{ row }">
        <div v-if="userFromRow(row)" class="users-view__actions">
          <AppButton
            v-if="userFromRow(row)!.role === 'admin'"
            size="sm"
            variant="secondary"
            @click="onDemote(userFromRow(row)!)"
          >
            {{ t("pages.admin.users.demote") }}
          </AppButton>
          <AppButton
            v-else
            size="sm"
            variant="secondary"
            @click="onPromote(userFromRow(row)!)"
          >
            {{ t("pages.admin.users.promote") }}
          </AppButton>

          <AppButton
            v-if="userFromRow(row)!.is_active"
            size="sm"
            variant="secondary"
            @click="onDeactivate(userFromRow(row)!)"
          >
            {{ t("pages.admin.users.deactivate") }}
          </AppButton>
          <AppButton
            v-else
            size="sm"
            variant="secondary"
            @click="onActivate(userFromRow(row)!)"
          >
            {{ t("pages.admin.users.activate") }}
          </AppButton>

          <AppButton
            size="sm"
            variant="danger"
            icon="trash"
            :aria-label="t('pages.admin.users.delete')"
            @click="onDelete(userFromRow(row)!)"
          />
        </div>
      </template>
    </AppTable>

    <div v-if="hasMore" class="users-view__load-more">
      <AppButton :loading="loading" :disabled="loading" @click="loadMore">
        {{ t("pages.admin.users.loadMore") }}
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.users-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.users-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.users-view__header :deep(.search-bar) {
  flex: 1;
  min-width: 16rem;
  max-width: 24rem;
}

.users-view__bulk-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.users-view__bulk-actions {
  display: flex;
  gap: var(--space-2);
}

.users-view__error {
  color: var(--color-danger);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
}

.users-view__status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 0.875rem;
  font-weight: 500;
}

.users-view__status--active::before,
.users-view__status--inactive::before {
  content: "";
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
}

.users-view__status--active::before {
  background-color: var(--color-success, #22c55e);
}

.users-view__status--inactive::before {
  background-color: var(--color-text-muted);
}

.users-view__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

.users-view__load-more {
  display: flex;
  justify-content: center;
}
</style>
