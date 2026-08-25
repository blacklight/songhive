import { computed, toValue, type ComputedRef, type MaybeRef } from "vue";
import { useAuthStore } from "@/stores/auth";

export interface Manageable {
  owner_id?: string | null;
}

export function useCanManage(ownerId?: MaybeRef<string | null | undefined>) {
  const authStore = useAuthStore();

  const canManage: ComputedRef<boolean> = computed(() => {
    if (!authStore.isAuthenticated) return false;
    if (authStore.isAdmin) return true;
    const id = toValue(ownerId);
    return !!id && !!authStore.user && authStore.user.id === id;
  });

  return { canManage };
}

export function canManageItem(
  authStore: ReturnType<typeof useAuthStore>,
  item?: Manageable | null,
): boolean {
  if (!authStore.isAuthenticated) return false;
  if (authStore.isAdmin) return true;
  const ownerId = item?.owner_id;
  return !!ownerId && !!authStore.user && authStore.user.id === ownerId;
}
