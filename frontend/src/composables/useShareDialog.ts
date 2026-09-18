import { ref } from "vue";
import type { ShareItemType } from "@/api/shares";

export interface ShareTarget {
  itemType: ShareItemType;
  itemId: string;
  title: string;
  ownerId: string | null;
  visibility: string | null;
  /** Direct media download URL for tracks (the track ``audio_url``). */
  downloadUrl: string | null;
}

export function useShareDialog() {
  const shareOpen = ref(false);
  const shareTarget = ref<ShareTarget | null>(null);

  function openShare(
    itemType: ShareItemType,
    itemId: string,
    title: string,
    ownerId?: string | null,
    visibility?: string | null,
    downloadUrl?: string | null,
  ) {
    shareTarget.value = {
      itemType,
      itemId,
      title,
      ownerId: ownerId ?? null,
      visibility: visibility ?? null,
      downloadUrl: downloadUrl ?? null,
    };
    shareOpen.value = true;
  }

  function closeShare() {
    shareOpen.value = false;
  }

  return {
    shareOpen,
    shareTarget,
    openShare,
    closeShare,
  };
}
