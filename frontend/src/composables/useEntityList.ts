import { ref, type Ref } from "vue";
import { getApiErrorMessage } from "@/api/client";
import { useDebounce } from "./useDebounce";

export interface EntityListParams {
  q?: string;
  limit: number;
  offset: number;
  [k: string]: unknown;
}

export function useEntityList<T>(
  fetcher: (params: EntityListParams) => Promise<T[]>,
  defaultLimit = 20,
) {
  const items: Ref<T[]> = ref([]);
  const loading = ref(false);
  const error: Ref<string | null> = ref(null);
  const query = ref("");
  const limit = ref(defaultLimit);
  const offset = ref(0);
  const hasMore = ref(false);
  const lastWasReset = ref(false);

  async function load(reset = false) {
    if (loading.value) return;

    lastWasReset.value = reset;
    loading.value = true;
    error.value = null;

    try {
      const targetOffset = reset ? 0 : offset.value;
      const result = await fetcher({
        q: query.value,
        limit: limit.value,
        offset: targetOffset,
      });

      if (reset) {
        items.value = result;
        offset.value = 0;
      } else {
        items.value = [...items.value, ...result];
        offset.value = targetOffset;
      }

      hasMore.value = result.length === limit.value;
    } catch (err) {
      error.value =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : "Unknown error");
      hasMore.value = false;
    } finally {
      loading.value = false;
    }
  }

  function loadMore() {
    if (loading.value) return;
    offset.value += limit.value;
    return load();
  }

  const search = useDebounce((q: string) => {
    query.value = q;
    offset.value = 0;
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
    offset,
    hasMore,
    load,
    loadMore,
    search,
    refresh,
    retry,
  };
}
