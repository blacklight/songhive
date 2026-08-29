<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { useMediaQuery } from "@/composables/useMediaQuery";
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
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppTable from "@/components/ui/AppTable.vue";
import SearchBar from "@/components/ui/SearchBar.vue";

const { t } = useI18n();
const isWide = useMediaQuery("(min-width: 1280px)", true);
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
  {
    key: "select",
    label: "",
    width: "3rem",
    align: "center" as const,
  },
  {
    key: "username",
    label: t("pages.admin.users.username"),
    width: "18%",
  },
  {
    key: "email",
    label: t("pages.admin.users.email"),
    width: "32%",
  },
  {
    key: "role",
    label: t("pages.admin.users.role"),
    width: "10%",
    align: "center" as const,
  },
  {
    key: "status",
    label: t("pages.admin.users.status"),
    width: "15%",
  },
  {
    key: "actions",
    label: t("pages.admin.users.actions"),
    align: "right" as const,
    width: "22%",
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

function userStatusLabel(user: AdminUserResponse): string {
  return user.is_active
    ? t("pages.admin.users.active")
    : t("pages.admin.users.inactive");
}

function statusClasses(user: AdminUserResponse): string[] {
  return [
    "users-view__status",
    user.is_active
      ? "users-view__status--active"
      : "users-view__status--inactive",
  ];
}

function roleClasses(role: AdminUserResponse["role"]): string[] {
  return ["users-view__role", `users-view__role--${role}`];
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
      <div class="users-view__filters">
        <SearchBar
          :model-value="query"
          :debounce="0"
          :placeholder="t('pages.admin.users.searchPlaceholder')"
          @update:model-value="search"
        />
      </div>
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

    <div v-if="loading && items.length === 0" class="users-view__loading">
      <AppSpinner />
    </div>

    <template v-else-if="items.length > 0">
      <AppTable
        v-if="isWide"
        :columns="columns"
        :rows="items as unknown as Record<string, unknown>[]"
        :row-key="(row) => String(row.id)"
        class="users-view__table"
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

        <template #row-username="{ row }">
          <span class="users-view__username">
            {{ userFromRow(row)?.username }}
          </span>
        </template>

        <template #row-email="{ row }">
          <span class="users-view__email" :title="userFromRow(row)?.email">
            {{ userFromRow(row)?.email }}
          </span>
        </template>

        <template #row-role="{ row }">
          <span
            v-if="userFromRow(row)"
            :class="roleClasses(userFromRow(row)!.role)"
          >
            {{ userFromRow(row)!.role }}
          </span>
        </template>

        <template #row-status="{ row }">
          <span
            v-if="userFromRow(row)"
            :class="statusClasses(userFromRow(row)!)"
          >
            {{ userStatusLabel(userFromRow(row)!) }}
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

      <ul v-else class="users-view__cards" role="list">
        <li v-for="user in items" :key="user.id" class="users-view__card">
          <div class="users-view__card-header">
            <AppCheckbox
              :model-value="isSelected(user)"
              @update:model-value="toggleSelect(user, $event as boolean)"
            />
            <span class="users-view__card-username" :title="user.username">
              {{ user.username }}
            </span>
            <span :class="statusClasses(user)">
              {{ userStatusLabel(user) }}
            </span>
          </div>

          <dl class="users-view__card-body">
            <div>
              <dt>{{ t("pages.admin.users.email") }}</dt>
              <dd>{{ user.email }}</dd>
            </div>
            <div>
              <dt>{{ t("pages.admin.users.role") }}</dt>
              <dd :class="roleClasses(user.role)">{{ user.role }}</dd>
            </div>
          </dl>

          <div class="users-view__card-footer">
            <AppButton
              v-if="user.role === 'admin'"
              size="sm"
              variant="secondary"
              @click="onDemote(user)"
            >
              {{ t("pages.admin.users.demote") }}
            </AppButton>
            <AppButton
              v-else
              size="sm"
              variant="secondary"
              @click="onPromote(user)"
            >
              {{ t("pages.admin.users.promote") }}
            </AppButton>

            <AppButton
              v-if="user.is_active"
              size="sm"
              variant="secondary"
              @click="onDeactivate(user)"
            >
              {{ t("pages.admin.users.deactivate") }}
            </AppButton>
            <AppButton
              v-else
              size="sm"
              variant="secondary"
              @click="onActivate(user)"
            >
              {{ t("pages.admin.users.activate") }}
            </AppButton>

            <AppButton
              size="sm"
              variant="danger"
              icon="trash"
              :aria-label="t('pages.admin.users.delete')"
              @click="onDelete(user)"
            />
          </div>
        </li>
      </ul>
    </template>

    <div v-else class="users-view__empty" role="status">
      {{
        query
          ? t("pages.admin.users.emptySearch")
          : t("pages.admin.users.empty")
      }}
    </div>

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
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.users-view__filters {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.users-view__filters > * {
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
  flex-wrap: wrap;
}

.users-view__error {
  color: var(--color-danger);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
}

.users-view__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.users-view__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.users-view__table :deep(.app-table) {
  table-layout: fixed;
  width: 100%;
}

.users-view__table :deep(.app-table__cell--select) {
  text-align: center;
  width: 3rem;
}

.users-view__table :deep(.app-table__cell--username) {
  font-weight: 500;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.users-view__table :deep(.app-table__cell--email) {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  overflow-wrap: anywhere;
}

.users-view__table :deep(.app-table__cell--role) {
  text-align: center;
  text-transform: capitalize;
  font-size: 0.875rem;
}

.users-view__table :deep(.app-table__cell--status) {
  white-space: nowrap;
}

.users-view__table :deep(.app-table__cell--actions) {
  text-align: right;
  white-space: nowrap;
}

.users-view__username {
  font-weight: 500;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.users-view__email {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.users-view__role {
  display: inline-block;
  text-transform: capitalize;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-text-muted);
}

.users-view__role--admin {
  width: 4rem;
  color: var(--color-accent-contrast);
  background-color: var(--color-accent);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-md);
  text-align: center;
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
  flex-wrap: wrap;
}

.users-view__cards {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.users-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.users-view__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 0;
}

.users-view__card-username {
  flex: 1;
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.users-view__card-body {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.users-view__card-body div {
  display: grid;
  grid-template-columns: 6rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.users-view__card-body dt {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}

.users-view__card-body dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.users-view__card-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--space-2);
}

.users-view__load-more {
  display: flex;
  justify-content: center;
}
</style>
