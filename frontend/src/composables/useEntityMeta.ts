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

  const ownerAvatarUrl = computed(() => {
    const e = toValue(entity);
    if (!e?.owner_id) return "";
    if (authStore.user?.id === e.owner_id) {
      return authStore.user.avatar_url ?? "";
    }
    return "";
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

  const visibilityIcon = computed(() => {
    const e = toValue(entity);
    if (!e?.visibility) return "";
    const icons: Record<string, string> = {
      private: "fas fa-lock",
      local: "fas fa-home",
      public: "fas fa-globe",
    };
    return icons[e.visibility] ?? "mdi-help-circle";
  });

  return { ownerName, ownerAvatarUrl, visibilityText, visibilityIcon };
}
