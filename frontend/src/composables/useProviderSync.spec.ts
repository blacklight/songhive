import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h, ref, nextTick } from "vue";
import { mount } from "@vue/test-utils";
import type { ProviderSyncStatus } from "@/api/providerSync";
import { useProviderSync, type UseProviderSync } from "./useProviderSync";

const handlers = new Map<string, Set<(event: unknown) => void>>();

vi.mock("@/api/ws", () => ({
  eventBus: {
    on: vi.fn((type: string, handler: (event: unknown) => void) => {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type)!.add(handler);
    }),
    off: vi.fn((type: string, handler: (event: unknown) => void) => {
      handlers.get(type)?.delete(handler);
    }),
    connect: vi.fn(),
  },
}));

function emitWs(payload: {
  kind?: string;
  entity_id?: string;
  state?: string;
}) {
  handlers
    .get("external_contents_refreshed")
    ?.forEach((h) => h({ type: "external_contents_refreshed", data: payload }));
}

function makeStatus(
  overrides: Partial<ProviderSyncStatus> = {},
): ProviderSyncStatus {
  return {
    provider_type: "tidal",
    state: "fresh",
    fetched_at: "2025-01-01T00:00:00Z",
    ttl_seconds: 21600,
    error: null,
    ...overrides,
  };
}

interface Setup {
  status: ReturnType<typeof ref<ProviderSyncStatus | null>>;
  entityId: ReturnType<typeof ref<string>>;
  sync: ReturnType<typeof vi.fn>;
  onRefreshed: ReturnType<typeof vi.fn>;
  onError: ReturnType<typeof vi.fn>;
}

function setup(overrides: Partial<Setup> = {}) {
  const status =
    overrides.status ?? ref<ProviderSyncStatus | null>(makeStatus());
  const entityId = overrides.entityId ?? ref("pl-1");
  const sync = overrides.sync ?? vi.fn().mockResolvedValue(makeStatus());
  const onRefreshed = overrides.onRefreshed ?? vi.fn();
  const onError = overrides.onError ?? vi.fn();

  let result!: UseProviderSync;
  const Host = defineComponent({
    setup() {
      result = useProviderSync({
        kind: "playlist",
        entityId,
        status,
        canSync: true,
        sync,
        onRefreshed,
        onError,
      });
      return () => h("div");
    },
  });
  const wrapper = mount(Host);
  return { result, status, entityId, sync, onRefreshed, onError, wrapper };
}

describe("useProviderSync", () => {
  beforeEach(() => {
    handlers.clear();
    vi.clearAllMocks();
  });

  it("seeds the status from the entity response", () => {
    const { result } = setup();
    expect(result.isProviderBacked.value).toBe(true);
    expect(result.syncStatus.value?.state).toBe("fresh");
    expect(result.isRefreshing.value).toBe(false);
  });

  it("reports non-provider entities as unbacked", () => {
    const { result } = setup({
      status: ref<ProviderSyncStatus | null>(null),
    });
    expect(result.isProviderBacked.value).toBe(false);
    expect(result.syncStatus.value).toBeNull();
  });

  it("flags never_fetched so callers can render a placeholder", () => {
    const { result } = setup({
      status: ref(makeStatus({ state: "never_fetched", fetched_at: null })),
    });
    expect(result.isNeverFetched.value).toBe(true);
  });

  it("syncNow refreshes and reloads contents on a synchronous fresh", async () => {
    const { result, sync, onRefreshed } = setup();
    sync.mockResolvedValue(
      makeStatus({ state: "fresh", fetched_at: "2025-06-01T00:00:00Z" }),
    );

    await result.syncNow();

    expect(sync).toHaveBeenCalledWith("pl-1");
    expect(result.syncStatus.value?.state).toBe("fresh");
    expect(result.syncing.value).toBe(false);
    expect(onRefreshed).toHaveBeenCalledOnce();
  });

  it("stays refreshing until the WS event settles the refresh", async () => {
    const { result, sync, onRefreshed } = setup();
    sync.mockResolvedValue(makeStatus({ state: "refreshing" }));

    await result.syncNow();
    expect(result.isRefreshing.value).toBe(true);
    expect(onRefreshed).not.toHaveBeenCalled();

    emitWs({ kind: "playlist", entity_id: "pl-1", state: "fresh" });
    await nextTick();

    expect(result.syncStatus.value?.state).toBe("fresh");
    expect(result.isRefreshing.value).toBe(false);
    expect(onRefreshed).toHaveBeenCalledOnce();
  });

  it("ignores WS events for other entities", async () => {
    const { result, sync, onRefreshed } = setup();
    sync.mockResolvedValue(makeStatus({ state: "refreshing" }));
    await result.syncNow();

    emitWs({ kind: "album", entity_id: "pl-1", state: "fresh" });
    emitWs({ kind: "playlist", entity_id: "other", state: "fresh" });
    await nextTick();

    expect(result.isRefreshing.value).toBe(true);
    expect(onRefreshed).not.toHaveBeenCalled();
  });

  it("surfaces sync failures through onError", async () => {
    const { result, sync, onError } = setup();
    sync.mockRejectedValue(new Error("boom"));

    await result.syncNow();

    expect(result.syncStatus.value?.state).toBe("error");
    expect(result.isRefreshing.value).toBe(false);
    expect(onError).toHaveBeenCalledWith("boom");
  });
});
