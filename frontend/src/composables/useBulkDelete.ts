import { computed, ref, type MaybeRef, toValue } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import { canManageItem } from "./useCanManage";

export interface ManageableItem {
  id: string;
  owner_id?: string | null;
}

export interface UseBulkDeleteOptions<T extends ManageableItem> {
  deleteOne: (id: string, recursive: boolean) => Promise<void>;
  refresh: () => Promise<void> | void;
  entitySingular: string;
  entityPlural: string;
  getName: (item: T) => string;
  getOwnerId?: (item: T) => string | null | undefined;
  recursive?: boolean;
  recursiveLabel?: MaybeRef<string | undefined>;
}

export function useBulkDelete<T extends ManageableItem>(
  options: UseBulkDeleteOptions<T>,
) {
  const { t } = useI18n();
  const authStore = useAuthStore();
  const toast = useToastStore();

  const bulkMode = ref(false);
  const selectedIds = ref<Set<string>>(new Set());
  const isDeleting = ref(false);
  const deleteModalOpen = ref(false);
  const deleteModalTitle = ref("");
  const deleteModalMessage = ref("");
  const deleteModalAllowRecursive = ref(false);
  const deleteModalLoading = ref(false);
  const pendingIds = ref<string[]>([]);
  const pendingName = ref<string>("");
  const isBulk = ref(false);

  const recursiveLabel = computed(() => toValue(options.recursiveLabel));

  function getOwnerId(item: T): string | null | undefined {
    return options.getOwnerId?.(item) ?? item.owner_id;
  }

  function canManage(item: T): boolean {
    return canManageItem(authStore, { owner_id: getOwnerId(item) });
  }

  function selected(items: T[]): T[] {
    return items.filter((item) => selectedIds.value.has(item.id));
  }

  function allSelected(items: T[]): boolean {
    const manageable = items.filter(canManage);
    return (
      manageable.length > 0 &&
      manageable.every((item) => selectedIds.value.has(item.id))
    );
  }

  function someSelected(items: T[]): boolean {
    const manageable = items.filter(canManage);
    const count = selectedIds.value.size;
    return count > 0 && !allSelected(manageable);
  }

  function toggleSelect(id: string) {
    if (selectedIds.value.has(id)) {
      selectedIds.value.delete(id);
    } else {
      selectedIds.value.add(id);
    }
  }

  function setSelected(items: T[], value: boolean) {
    for (const item of items) {
      if (!canManage(item)) continue;
      if (value) {
        selectedIds.value.add(item.id);
      } else {
        selectedIds.value.delete(item.id);
      }
    }
  }

  function toggleAll(items: T[]) {
    setSelected(items, !allSelected(items));
  }

  function enterBulkMode() {
    bulkMode.value = true;
  }

  function exitBulkMode() {
    bulkMode.value = false;
    selectedIds.value.clear();
  }

  function toggleBulkMode() {
    if (bulkMode.value) {
      exitBulkMode();
    } else {
      enterBulkMode();
    }
  }

  function openDeleteSingle(item: T) {
    pendingIds.value = [item.id];
    pendingName.value = options.getName(item);
    isBulk.value = false;
    deleteModalAllowRecursive.value = options.recursive ?? false;
    deleteModalTitle.value = t("browse.delete.title", {
      name: pendingName.value,
      entity: options.entitySingular,
    });
    deleteModalMessage.value = t("browse.delete.confirm", {
      name: pendingName.value,
      entity: options.entitySingular,
    });
    deleteModalOpen.value = true;
  }

  function openDeleteBulk(items: T[]) {
    const toDelete = items.filter((item) => selectedIds.value.has(item.id));
    if (toDelete.length === 0) return;

    if (toDelete.length === 1) {
      openDeleteSingle(toDelete[0]);
      return;
    }

    pendingIds.value = toDelete.map((item) => item.id);
    pendingName.value = "";
    isBulk.value = true;
    deleteModalAllowRecursive.value = options.recursive ?? false;
    deleteModalTitle.value = t("browse.delete.bulkTitle", {
      count: toDelete.length,
      entity: options.entityPlural,
    });
    deleteModalMessage.value = t("browse.delete.bulkConfirm", {
      count: toDelete.length,
      entity: options.entityPlural,
    });
    deleteModalOpen.value = true;
  }

  async function doDelete(ids: string[], recursive: boolean) {
    let deleted = 0;
    for (const id of ids) {
      try {
        await options.deleteOne(id, recursive);
        deleted++;
      } catch (err) {
        const entity =
          ids.length === 1 ? options.entitySingular : options.entityPlural;
        toast.push({
          type: "error",
          message: t("browse.delete.error", {
            entity,
            message: getApiErrorMessage(err),
          }),
        });
        break;
      }
    }

    if (deleted > 0) {
      const message =
        deleted === 1 && pendingName.value
          ? t("browse.delete.success", {
              name: pendingName.value,
              entity: options.entitySingular,
            })
          : t("browse.delete.bulkSuccess", {
              count: deleted,
              entity: options.entityPlural,
            });
      toast.push({ type: "success", message });
    }

    selectedIds.value.clear();
    exitBulkMode();
    await options.refresh();
  }

  async function confirmDelete(recursive: boolean) {
    deleteModalLoading.value = true;
    isDeleting.value = true;
    deleteModalOpen.value = false;

    await doDelete(pendingIds.value, recursive);

    isDeleting.value = false;
    deleteModalLoading.value = false;
    pendingIds.value = [];
    pendingName.value = "";
    isBulk.value = false;
  }

  function closeDeleteModal() {
    deleteModalOpen.value = false;
    deleteModalLoading.value = false;
    isDeleting.value = false;
    pendingIds.value = [];
    pendingName.value = "";
    isBulk.value = false;
  }

  return {
    bulkMode,
    selectedIds,
    isDeleting,
    deleteModalOpen,
    deleteModalTitle,
    deleteModalMessage,
    deleteModalAllowRecursive,
    deleteModalLoading,
    canManage,
    selected,
    allSelected,
    someSelected,
    toggleSelect,
    setSelected,
    toggleAll,
    enterBulkMode,
    exitBulkMode,
    toggleBulkMode,
    openDeleteSingle,
    openDeleteBulk,
    confirmDelete,
    closeDeleteModal,
    recursiveLabel,
  };
}
