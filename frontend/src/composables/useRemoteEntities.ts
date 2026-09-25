import { ref, watch, type Ref } from "vue";
import {
  listRemoteObjectsWithMeta,
  type RemoteObject,
  type RemoteResourceKind,
} from "@/api/remote";

/**
 * Cached remote (federated) resources of one kind for the browse grids —
 * the federated counterpart of the local entity list. The fetch tracks
 * the list's search box, collection toggle, and sort so remote entities
 * filter and order exactly like their local twins, and it paginates in
 * lockstep: ``loadMore`` appends the next remote page so remote entries
 * keep arriving as the local list grows rather than sitting as a fixed
 * first-page set. Policy denials and network errors degrade to an empty
 * list (or keep the loaded pages on a failed loadMore) rather than an
 * error banner: remote entries are strictly additive to the view.
 */
export function useRemoteEntities(
  kind: RemoteResourceKind,
  options: {
    collection: Ref<boolean>;
    query: Ref<string>;
    sortBy?: Ref<string>;
    sortDir?: Ref<"asc" | "desc">;
    limit?: number;
  },
) {
  const items = ref<RemoteObject[]>([]);
  const loading = ref(false);
  const loadingMore = ref(false);
  const hasMore = ref(false);
  const total = ref(0);
  const pageSize = options.limit ?? 20;
  // Filter/sort changes restart paging at offset 0 — the generation
  // counter drops a slow response that would otherwise append rows
  // fetched under the previous filters.
  let generation = 0;

  function fetchPage(offset: number) {
    return listRemoteObjectsWithMeta({
      resource_type: kind,
      q: options.query.value.trim() || undefined,
      collection: options.collection.value || undefined,
      limit: pageSize,
      offset,
      sort_by: options.sortBy?.value,
      sort_dir: options.sortDir?.value,
    });
  }

  async function load() {
    const gen = ++generation;
    loading.value = true;
    try {
      const res = await fetchPage(0);
      if (gen !== generation) return;
      items.value = res.items;
      total.value = res.total;
      hasMore.value = res.items.length < res.total;
    } catch {
      if (gen !== generation) return;
      items.value = [];
      total.value = 0;
      hasMore.value = false;
    } finally {
      if (gen === generation) loading.value = false;
    }
  }

  async function loadMore() {
    if (loading.value || loadingMore.value || !hasMore.value) return;
    const gen = generation;
    loadingMore.value = true;
    try {
      const res = await fetchPage(items.value.length);
      if (gen !== generation) return;
      items.value = [...items.value, ...res.items];
      total.value = res.total;
      hasMore.value = items.value.length < res.total;
    } catch {
      // Keep accumulated pages — a transient failure must not drop
      // remote entries that are already rendered in the merged list.
    } finally {
      // Only one loadMore runs at a time, so this call owns the flag even
      // when its result was superseded by a reset.
      loadingMore.value = false;
    }
  }

  const watchables: Ref<string | boolean | undefined>[] = [
    options.collection,
    options.query,
  ];
  if (options.sortBy) watchables.push(options.sortBy);
  if (options.sortDir) watchables.push(options.sortDir);
  watch(watchables, load, { immediate: true });

  return {
    items,
    loading,
    loadingMore,
    hasMore,
    total,
    reload: load,
    loadMore,
  };
}
