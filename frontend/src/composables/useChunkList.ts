import { computed, ref, type Ref } from "vue";
import { getApiErrorMessage } from "@/api/client";
import { useDebounce } from "./useDebounce";

export interface ChunkListParams {
  q?: string;
  limit: number;
  offset: number;
  around_track_id?: string;
  [k: string]: unknown;
}

export interface ChunkListResult<T> {
  items: T[];
  offset: number;
  total: number;
}

export type ChunkListFetcher<T> = (
  params: ChunkListParams,
) => Promise<ChunkListResult<T>>;

export function useChunkList<T>(
  fetcher: ChunkListFetcher<T>,
  defaultLimit = 20,
) {
  const items: Ref<T[]> = ref([]);
  const loading = ref(false);
  const error: Ref<string | null> = ref(null);
  const query = ref("");
  const limit = ref(defaultLimit);
  const startOffset = ref(0);
  const currentOffset = ref(0);
  const total = ref(0);
  const lastWasReset = ref(false);

  const hasMore = computed(
    () => startOffset.value + items.value.length < total.value,
  );
  const hasPrevious = computed(() => startOffset.value > 0);

  async function doLoad(
    targetOffset: number,
    aroundTrackId?: string,
    mode: "replace" | "prepend" | "append" = "replace",
  ) {
    if (loading.value) return;

    loading.value = true;
    error.value = null;

    try {
      const result = await fetcher({
        q: query.value,
        limit: limit.value,
        offset: targetOffset,
        around_track_id: aroundTrackId,
      });

      if (mode === "replace") {
        items.value = result.items;
        startOffset.value = result.offset;
      } else if (mode === "prepend") {
        const overlap = Math.max(0, startOffset.value - result.offset);
        const newItems = result.items.slice(0, overlap);
        items.value = [...newItems, ...items.value];
        startOffset.value = result.offset;
      } else {
        items.value = [...items.value, ...result.items];
      }

      currentOffset.value = result.offset;
      total.value = result.total;
    } catch (err) {
      error.value =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : "Unknown error");
    } finally {
      loading.value = false;
    }
  }

  async function load(reset = false, aroundTrackId?: string) {
    lastWasReset.value = reset;
    if (reset) {
      items.value = [];
      startOffset.value = 0;
      currentOffset.value = 0;
    }
    const targetOffset = reset ? 0 : currentOffset.value;
    return doLoad(targetOffset, aroundTrackId, "replace");
  }

  function loadMore() {
    if (loading.value || !hasMore.value) return;
    const targetOffset = startOffset.value + items.value.length;
    return doLoad(targetOffset, undefined, "append");
  }

  function loadPrevious() {
    if (loading.value || !hasPrevious.value) return;
    const targetOffset = Math.max(0, startOffset.value - limit.value);
    return doLoad(targetOffset, undefined, "prepend");
  }

  function loadAround(aroundTrackId: string) {
    lastWasReset.value = true;
    items.value = [];
    startOffset.value = 0;
    currentOffset.value = 0;
    return doLoad(0, aroundTrackId, "replace");
  }

  const search = useDebounce((q: string) => {
    query.value = q;
    return load(true);
  }, 300);

  function refresh() {
    return load(true);
  }

  function retry() {
    return load(lastWasReset.value);
  }

  return {
    items,
    loading,
    error,
    query,
    limit,
    offset: currentOffset,
    startOffset,
    total,
    hasMore,
    hasPrevious,
    load,
    loadMore,
    loadPrevious,
    loadAround,
    search,
    refresh,
    retry,
  };
}
