import { computed, toValue, type MaybeRef } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";

export interface EntityMeta {
  owner_id?: string | null;
  visibility?: string;
}

export function useEntityMeta(entity: MaybeRef<EntityMeta | null | undefined>) {
  const { t } = useI18n();
  const authStore = useAuthStore();

  const ownerName = computed(() => {
    const e = toValue(entity);
    if (!e?.owner_id) return "";
    if (authStore.user?.id === e.owner_id) {
      return authStore.user.display_name ?? authStore.user.username;
    }
    return e.owner_id;
  });

  const visibilityText = computed(() => {
    const e = toValue(entity);
    if (!e?.visibility) return "";
    const labels: Record<string, string> = {
      private: t("browse.visibility.private"),
      local: t("browse.visibility.local"),
      public: t("browse.visibility.public"),
    };
    return labels[e.visibility] ?? e.visibility;
  });

  return { ownerName, visibilityText };
}
