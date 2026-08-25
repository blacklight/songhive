import { computed, ref, type ComputedRef, type MaybeRef, toValue } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import { canManageItem } from "./useCanManage";

export interface UseEntityDeleteOptions {
  delete: (id: string, recursive: boolean) => Promise<void>;
  entity: string;
  redirectTo?: string;
  allowRecursive?: boolean;
  recursiveLabel?: MaybeRef<string | undefined>;
  getName: () => string;
  getOwnerId: () => string | null | undefined;
}

export function useEntityDelete(options: UseEntityDeleteOptions) {
  const { t } = useI18n();
  const router = useRouter();
  const authStore = useAuthStore();
  const toast = useToastStore();

  const modalOpen = ref(false);
  const modalTitle = ref("");
  const modalMessage = ref("");
  const modalLoading = ref(false);
  const pendingId = ref<string | null>(null);
  const allowRecursive = ref(false);
  const recursiveLabel = computed(() => toValue(options.recursiveLabel));

  const canDelete: ComputedRef<boolean> = computed(() => {
    const ownerId = options.getOwnerId();
    return canManageItem(authStore, ownerId ? { owner_id: ownerId } : null);
  });

  function open(id: string) {
    pendingId.value = id;
    const name = options.getName();
    modalTitle.value = t("browse.delete.title", {
      name,
      entity: options.entity,
    });
    modalMessage.value = t("browse.delete.confirm", {
      name,
      entity: options.entity,
    });
    allowRecursive.value = options.allowRecursive ?? false;
    modalOpen.value = true;
  }

  function close() {
    modalOpen.value = false;
    modalLoading.value = false;
    pendingId.value = null;
  }

  async function confirm(recursive: boolean) {
    if (!pendingId.value) return;

    modalLoading.value = true;
    try {
      await options.delete(pendingId.value, recursive);
      toast.push({
        type: "success",
        message: t("browse.delete.success", {
          name: options.getName(),
          entity: options.entity,
        }),
      });
      close();
      if (options.redirectTo) {
        await router.push(options.redirectTo);
      }
    } catch (err) {
      toast.push({
        type: "error",
        message: t("browse.delete.error", {
          entity: options.entity,
          message: getApiErrorMessage(err),
        }),
      });
    } finally {
      modalLoading.value = false;
    }
  }

  return {
    canDelete,
    modalOpen,
    modalTitle,
    modalMessage,
    modalLoading,
    allowRecursive,
    recursiveLabel,
    open,
    close,
    confirm,
  };
}
