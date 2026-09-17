import { onMounted, ref, type Ref } from "vue";
import { getApiErrorMessage } from "@/api/client";

export interface ShelfData<T> {
  items: Ref<T[]>;
  loading: Ref<boolean>;
  error: Ref<string | null>;
  retry: () => Promise<void>;
}

/**
 * Fetch the items of a single home-page shelf on mount.
 *
 * Each shelf is independent: a failure is reported through ``error`` so the
 * section can offer an inline retry without affecting the rest of the page,
 * and an empty result lets the section remove itself entirely.
 */
export function useShelfData<T>(fetcher: () => Promise<T[]>): ShelfData<T> {
  const items = ref<T[]>([]) as Ref<T[]>;
  const loading = ref(true);
  const error: Ref<string | null> = ref(null);

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      items.value = await fetcher();
    } catch (err) {
      error.value =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : "Unknown error");
    } finally {
      loading.value = false;
    }
  }

  onMounted(load);

  return { items, loading, error, retry: load };
}
