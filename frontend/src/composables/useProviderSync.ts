import {
  computed,
  onUnmounted,
  ref,
  toValue,
  watch,
  type ComputedRef,
  type MaybeRef,
  type Ref,
} from "vue";
import { eventBus, type WsEvent } from "@/api/ws";
import type { ProviderSyncStatus } from "@/api/providerSync";

const WS_EVENT = "external_contents_refreshed";

export interface UseProviderSyncOptions {
  /** Entity kind used to match ``external_contents_refreshed`` events. */
  kind: "playlist" | "album";
  /** Local entity id (the playlist/album id, not the provider key). */
  entityId: MaybeRef<string | null | undefined>;
  /** ``provider_sync`` payload from the latest entity GET. */
  status: MaybeRef<ProviderSyncStatus | null | undefined>;
  /** Whether the current user may force a refresh (owner only). */
  canSync: MaybeRef<boolean>;
  /** ``POST /{kind}s/{id}/provider-sync`` caller. */
  sync: (id: string) => Promise<ProviderSyncStatus>;
  /** Reload the container's track list after a refresh completes. */
  onRefreshed?: () => void | Promise<void>;
  /** Surface a transient error message (e.g. toast). */
  onError?: (message: string) => void;
}

export interface UseProviderSync {
  /** Current provider-sync state; null when the entity isn't provider-backed. */
  syncStatus: Ref<ProviderSyncStatus | null>;
  isProviderBacked: ComputedRef<boolean>;
  /** True while a refresh is in flight (optimistic or server-reported). */
  isRefreshing: ComputedRef<boolean>;
  /** Contents were never fetched — render a placeholder, not an empty list. */
  isNeverFetched: ComputedRef<boolean>;
  canSync: ComputedRef<boolean>;
  syncing: Ref<boolean>;
  syncNow: () => Promise<void>;
}

/**
 * Track the lazy-contents state of a provider-backed playlist/album.
 *
 * The status seeds from the entity response, flips to ``refreshing``
 * optimistically on ``syncNow``, and settles on the
 * ``external_contents_refreshed`` WS event (or the POST response itself when
 * the refresh completes synchronously).
 */
export function useProviderSync(
  options: UseProviderSyncOptions,
): UseProviderSync {
  const syncStatus = ref<ProviderSyncStatus | null>(null);
  const syncing = ref(false);
  const canSync = computed(() => toValue(options.canSync));

  watch(
    () => toValue(options.status),
    (status) => {
      if (status) syncStatus.value = status;
      else if (syncStatus.value?.state !== "refreshing") {
        syncStatus.value = null;
      }
    },
    { immediate: true },
  );

  function onWsEvent(event: WsEvent) {
    const data = event.data as
      { kind?: string; entity_id?: string; state?: string } | undefined;
    if (
      data?.kind !== options.kind ||
      data.entity_id !== toValue(options.entityId)
    ) {
      return;
    }
    const state = data.state === "error" ? "error" : "fresh";
    syncStatus.value = syncStatus.value
      ? { ...syncStatus.value, state }
      : syncStatus.value;
    syncing.value = false;
    void options.onRefreshed?.();
  }

  eventBus.on(WS_EVENT, onWsEvent);
  eventBus.connect();
  onUnmounted(() => {
    eventBus.off(WS_EVENT, onWsEvent);
  });

  async function syncNow() {
    const id = toValue(options.entityId);
    if (!id || syncing.value) return;
    syncing.value = true;
    if (syncStatus.value) {
      syncStatus.value = { ...syncStatus.value, state: "refreshing" };
    }
    try {
      const status = await options.sync(id);
      syncStatus.value = status;
      if (status.state === "fresh" || status.state === "error") {
        syncing.value = false;
        if (status.state === "fresh") await options.onRefreshed?.();
      }
      // "refreshing"/"never_fetched": wait for the WS event to settle.
    } catch (err) {
      syncing.value = false;
      if (syncStatus.value) {
        syncStatus.value = { ...syncStatus.value, state: "error" };
      }
      options.onError?.(err instanceof Error ? err.message : String(err));
    }
  }

  return {
    syncStatus,
    isProviderBacked: computed(() => syncStatus.value !== null),
    isRefreshing: computed(
      () => syncing.value || syncStatus.value?.state === "refreshing",
    ),
    isNeverFetched: computed(() => syncStatus.value?.state === "never_fetched"),
    canSync,
    syncing,
    syncNow,
  };
}
