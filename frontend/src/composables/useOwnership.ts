import { computed, type ComputedRef } from "vue";
import { useAuthStore } from "@/stores/auth";

export function useOwnership(ownerId?: string | null) {
  const authStore = useAuthStore();

  const isOwner: ComputedRef<boolean> = computed(
    () => !!ownerId && !!authStore.user && authStore.user.id === ownerId,
  );

  return {
    isOwner,
    currentUser: authStore.user,
  };
}
