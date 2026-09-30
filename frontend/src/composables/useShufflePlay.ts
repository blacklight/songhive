import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { getApiErrorMessage } from "@/api/client";
import { usePlayerStore, type QueueRefill } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { isUnplayable } from "@/player/playable";
import type { QueueTrack } from "@/player/types";

/** Tracks requested per random chunk — the listing API's max page size. */
export const SHUFFLE_CHUNK_SIZE = 100;

/**
 * Collection-wide "shuffle play". The fetcher returns a fresh random chunk
 * of queue tracks from the collection — typically a ``sort_by=random``
 * listing call. ``shufflePlay`` starts playback on the first chunk and hands
 * the same fetcher to the player store, which re-invokes it to top the queue
 * up as playback approaches its end. Shuffling therefore covers the whole
 * collection instead of only the rows the view happened to load.
 */
export function useShufflePlay(fetch: () => Promise<QueueTrack[]>) {
  const player = usePlayerStore();
  const toastStore = useToastStore();
  const { t } = useI18n();
  const loading = ref(false);

  // A page shorter than the chunk size means the collection is fully
  // covered — no further fetches, and the empty refill drains the store's
  // refill state without another request.
  let exhausted = false;
  const refill: QueueRefill = async () => {
    if (exhausted) return [];
    const raw = await fetch();
    if (raw.length < SHUFFLE_CHUNK_SIZE) exhausted = true;
    return raw.filter((track) => !isUnplayable(track));
  };

  async function shufflePlay() {
    if (loading.value) return;
    loading.value = true;
    try {
      exhausted = false;
      const chunk = await refill();
      if (chunk.length === 0) {
        toastStore.push({
          type: "info",
          message: t("browse.detail.shuffleEmpty"),
        });
        return;
      }
      player.shuffleAll(chunk, refill);
    } catch (err) {
      toastStore.push({
        type: "error",
        message:
          getApiErrorMessage(err) ||
          (err instanceof Error ? err.message : t("errors.unknown")),
      });
    } finally {
      loading.value = false;
    }
  }

  return { shufflePlay, loading };
}
