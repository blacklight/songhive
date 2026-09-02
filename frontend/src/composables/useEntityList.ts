import { ref, type Ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getApiErrorMessage } from "@/api/client";
import { useDebounce } from "./useDebounce";

export interface EntityListParams {
  q?: string;
  limit: number;
  offset: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  [k: string]: unknown;
}

export interface UseEntityListOptions {
  defaultLimit?: number;
  defaultSortBy?: string;
  defaultSortDir?: "asc" | "desc";
  syncQuery?: boolean;
  queryKey?: string;
}

export function useEntityList<T>(
  fetcher: (params: EntityListParams) => Promise<T[]>,
  options: number | UseEntityListOptions = {},
) {
  const opts: UseEntityListOptions =
    typeof options === "number" ? { defaultLimit: options } : options;

  const defaultLimit = opts.defaultLimit ?? 20;
  const defaultSortBy = opts.defaultSortBy ?? "name";
  const defaultSortDir = opts.defaultSortDir ?? "asc";
  const syncQuery = opts.syncQuery ?? false;
  const queryKey = opts.queryKey ?? "";

  const byKey = queryKey ? `${queryKey}_sort_by` : "sort_by";
  const dirKey = queryKey ? `${queryKey}_sort_dir` : "sort_dir";

  const route = syncQuery ? useRoute() : null;
  const router = syncQuery ? useRouter() : null;

  const items: Ref<T[]> = ref([]);
  const loading = ref(false);
  const loadingMore = ref(false);
  const error: Ref<string | null> = ref(null);
  const query = ref("");
  const limit = ref(defaultLimit);
  const offset = ref(0);
  const hasMore = ref(false);
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
        sort_by: sortBy.value,
        sort_dir: sortDir.value,
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

  async function loadMore() {
    if (loading.value || loadingMore.value || !hasMore.value) return;

    loadingMore.value = true;
    offset.value += limit.value;
    try {
      await load();
    } finally {
      loadingMore.value = false;
    }
  }

  const search = useDebounce((q: string) => {
    query.value = q;
    offset.value = 0;
    return load(true);
  }, 300);

  async function setSort(field: string, dir: "asc" | "desc") {
    if (field === sortBy.value && dir === sortDir.value) return;
    sortBy.value = field;
    sortDir.value = dir;
    offset.value = 0;
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
    error,
    query,
    limit,
    offset,
    hasMore,
    total,
    sortBy,
    sortDir,
    load,
    loadMore,
    search,
    setSort,
    refresh,
    retry,
  };
}
