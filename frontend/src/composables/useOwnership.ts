import { computed, toValue, type ComputedRef, type MaybeRef } from "vue";
import { useAuthStore } from "@/stores/auth";

export function useOwnership(ownerId?: MaybeRef<string | null | undefined>) {
  const authStore = useAuthStore();

  const isOwner: ComputedRef<boolean> = computed(() => {
    const id = toValue(ownerId);
    return !!id && !!authStore.user && authStore.user.id === id;
  });

  return {
    isOwner,
    currentUser: authStore.user,
  };
}
