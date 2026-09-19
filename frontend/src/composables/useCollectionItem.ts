import { computed, ref, type Ref } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import {
  addToCollection,
  removeFromCollection,
  type CollectionItemType,
} from "@/api/collection";
import type { ActionItem } from "@/components/ui/EntityActions.vue";
import { useOwnership } from "./useOwnership";

export interface CollectibleEntity {
  id: string;
  owner_id?: string | null;
  in_collection?: boolean;
}

/**
 * Save/remove an entity in the current user's collection.
 *
 * ``entity`` is mutated in place: its ``in_collection`` flag flips after each
 * successful call. Owned entities never expose the action — content the user
 * owns is inherently part of their collection and cannot be unsaved.
 */
export function useCollectionItem(
  itemType: CollectionItemType,
  entity: Ref<CollectibleEntity | null>,
) {
  const { t } = useI18n();
  const authStore = useAuthStore();
  const toastStore = useToastStore();
  const saving = ref(false);

  const { isOwner } = useOwnership(
    computed(() => entity.value?.owner_id ?? null),
  );

  const canToggle = computed(
    () => authStore.isAuthenticated && entity.value !== null && !isOwner.value,
  );

  const collectionAction = computed<ActionItem>(() => {
    const saved = Boolean(entity.value?.in_collection);
    return {
      key: "collection",
      label: saved
        ? t("browse.collection.remove")
        : t("browse.collection.save"),
      icon: saved ? "xmark" : "bookmark",
      variant: "secondary" as const,
      visible: canToggle.value,
      loading: saving.value,
    };
  });

  async function toggleCollection() {
    const target = entity.value;
    if (!target || saving.value || !canToggle.value) return;
    saving.value = true;
    const wasSaved = Boolean(target.in_collection);
    try {
      if (wasSaved) {
        await removeFromCollection(itemType, target.id);
        target.in_collection = false;
        toastStore.push({
          type: "success",
          message: t("browse.collection.removeSuccess"),
        });
      } else {
        await addToCollection(itemType, target.id);
        target.in_collection = true;
        toastStore.push({
          type: "success",
          message: t("browse.collection.saveSuccess"),
        });
      }
    } catch (err) {
      const message =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown"));
      toastStore.push({
        type: "error",
        message: wasSaved
          ? t("browse.collection.removeError", { message })
          : t("browse.collection.saveError", { message }),
      });
    } finally {
      saving.value = false;
    }
  }

  return { collectionAction, toggleCollection };
}
