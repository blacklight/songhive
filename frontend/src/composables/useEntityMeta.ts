import { computed, toValue, type MaybeRef } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import type { components } from "@/api/types";

type UserSummary = components["schemas"]["UserSummary"];

export interface EntityMeta {
  owner_id?: string | null;
  visibility?: string;
  owner?: UserSummary | null;
}

export function useEntityMeta(entity: MaybeRef<EntityMeta | null | undefined>) {
  const { t } = useI18n();
  const authStore = useAuthStore();

  const owner = computed<UserSummary | null>(() => {
    const e = toValue(entity);
    if (e?.owner) return e.owner;
    if (e?.owner_id && authStore.user?.id === e.owner_id) {
      return authStore.user as UserSummary;
    }
    return null;
  });

  const ownerName = computed(
    () => owner.value?.display_name || owner.value?.username || "",
  );

  const ownerAvatarUrl = computed(() => owner.value?.avatar_url ?? "");

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

  return { owner, ownerName, ownerAvatarUrl, visibilityText, visibilityIcon };
}
