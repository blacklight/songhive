import { computed, ref, type Ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getApiErrorMessage } from "@/api/client";
import { useDebounce } from "./useDebounce";

export interface ChunkListParams {
  q?: string;
  limit: number;
  offset: number;
  around_track_id?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
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

export interface UseChunkListOptions {
  defaultLimit?: number;
  defaultSortBy?: string;
  defaultSortDir?: "asc" | "desc";
  syncQuery?: boolean;
  queryKey?: string;
}

export function useChunkList<T>(
  fetcher: ChunkListFetcher<T>,
  options: number | UseChunkListOptions = {},
) {
  const opts: UseChunkListOptions =
    typeof options === "number" ? { defaultLimit: options } : options;

  const defaultLimit = opts.defaultLimit ?? 20;
  const defaultSortBy = opts.defaultSortBy ?? "created_at";
  const defaultSortDir = opts.defaultSortDir ?? "desc";
  const syncQuery = opts.syncQuery ?? false;
  const queryKey = opts.queryKey ?? "";

  const byKey = queryKey ? `${queryKey}_sort_by` : "sort_by";
  const dirKey = queryKey ? `${queryKey}_sort_dir` : "sort_dir";

  const route = syncQuery ? useRoute() : null;
  const router = syncQuery ? useRouter() : null;

  const items: Ref<T[]> = ref([]);
  const loading = ref(false);
  const loadingMore = ref(false);
  const loadingPrevious = ref(false);
  const error: Ref<string | null> = ref(null);
  const query = ref("");
  const limit = ref(defaultLimit);
  const startOffset = ref(0);
  const currentOffset = ref(0);
  const total = ref(0);
  const lastWasReset = ref(false);

  function getInitialSortBy(): string {
    if (!route) return defaultSortBy;
    const value = route.query[byKey];
    return typeof value === "string" ? value : defaultSortBy;
  }

  function getInitialSortDir(): "asc" | "desc" {
    if (!route) return defaultSortDir;
    const value = route.query[dirKey];
    return value === "desc" ? "desc" : value === "asc" ? "asc" : defaultSortDir;
  }

  const sortBy = ref<string>(getInitialSortBy());
  const sortDir = ref<"asc" | "desc">(getInitialSortDir());

  const hasMore = computed(
    () => startOffset.value + items.value.length < total.value,
  );
  const hasPrevious = computed(() => startOffset.value > 0);

  async function updateQueryString() {
    if (!router || !route) return;
    const newQuery = { ...route.query };
    if (sortBy.value === defaultSortBy && sortDir.value === defaultSortDir) {
      delete newQuery[byKey];
      delete newQuery[dirKey];
    } else {
      newQuery[byKey] = sortBy.value;
      newQuery[dirKey] = sortDir.value;
    }
    await router.replace({ query: newQuery });
  }

  function isBusy() {
    return loading.value || loadingMore.value || loadingPrevious.value;
  }

  async function doLoad(
    targetOffset: number,
    aroundTrackId?: string,
    mode: "replace" | "prepend" | "append" = "replace",
  ) {
    if (isBusy()) return;

    loading.value = true;
    if (mode === "append") {
      loadingMore.value = true;
    } else if (mode === "prepend") {
      loadingPrevious.value = true;
    }
    error.value = null;

    try {
      const result = await fetcher({
        q: query.value,
        limit: limit.value,
        offset: targetOffset,
        around_track_id: aroundTrackId,
        sort_by: sortBy.value,
        sort_dir: sortDir.value,
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
      if (mode === "append") {
        loadingMore.value = false;
      } else if (mode === "prepend") {
        loadingPrevious.value = false;
      }
    }
  }

  async function load(reset = false, aroundTrackId?: string) {
    if (isBusy()) return;
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
    if (isBusy() || !hasMore.value) return;
    const targetOffset = startOffset.value + items.value.length;
    return doLoad(targetOffset, undefined, "append");
  }

  function loadPrevious() {
    if (isBusy() || !hasPrevious.value) return;
    const targetOffset = Math.max(0, startOffset.value - limit.value);
    return doLoad(targetOffset, undefined, "prepend");
  }

  function loadAround(aroundTrackId: string) {
    if (isBusy()) return;
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

  async function setSort(field: string, dir: "asc" | "desc") {
    if (field === sortBy.value && dir === sortDir.value) return;
    sortBy.value = field;
    sortDir.value = dir;
    currentOffset.value = 0;
    startOffset.value = 0;
    await updateQueryString();
    return load(true);
  }

  function refresh() {
    return load(true);
  }

  function retry() {
    return load(lastWasReset.value);
  }

  return {
    items,
    loading,
    loadingMore,
    loadingPrevious,
    error,
    query,
    limit,
    offset: currentOffset,
    startOffset,
    total,
    sortBy,
    sortDir,
    hasMore,
    hasPrevious,
    load,
    loadMore,
    loadPrevious,
    loadAround,
    search,
    setSort,
    refresh,
    retry,
  };
}
