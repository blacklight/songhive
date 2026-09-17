import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import * as timelineApi from "@/api/timeline";
import type { ActivityResponse } from "@/api/activities";
import { readStoredScope, useTimelineStore } from "./timeline";

vi.mock("@/api/timeline", () => ({
  listTimeline: vi.fn(),
}));

const STORAGE_KEY = "songhive.home.timelineScope";

function makeActivity(id: string): ActivityResponse {
  return {
    id,
    entity_type: "track",
    entity_id: "track-1",
    activity_type: "create",
    source_type: "local",
    source_actor: "https://example.com/users/alice",
    source_id: `https://example.com/objects/${id}`,
    visibility: "public",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    quote_count: 0,
    liked: false,
    boosted: false,
    can_interact: false,
  };
}

function page(
  activities: ActivityResponse[],
  next_cursor: string | null = null,
) {
  return { activities, next_cursor };
}

describe("useTimelineStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(timelineApi.listTimeline).mockResolvedValue(page([]));
  });

  it("defaults anonymous visitors to the instance scope", async () => {
    const store = useTimelineStore();

    await store.init(false);

    expect(store.scope).toBe("instance");
    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "instance" }),
    );
  });

  it("defaults authenticated users to the mine scope", async () => {
    vi.mocked(timelineApi.listTimeline).mockResolvedValue(
      page([makeActivity("a1")]),
    );
    const store = useTimelineStore();

    await store.init(true);

    expect(store.scope).toBe("mine");
    expect(store.items).toHaveLength(1);
    expect(timelineApi.listTimeline).toHaveBeenCalledTimes(1);
  });

  it("falls back to the instance scope when the user has no own activity", async () => {
    const listTimeline = vi.mocked(timelineApi.listTimeline);
    listTimeline
      .mockResolvedValueOnce(page([])) // scope=mine comes back empty
      .mockResolvedValueOnce(page([makeActivity("a1")])); // instance fallback
    const store = useTimelineStore();

    await store.init(true);

    expect(store.scope).toBe("instance");
    expect(store.items).toHaveLength(1);
    expect(listTimeline).toHaveBeenLastCalledWith(
      expect.objectContaining({ scope: "instance" }),
    );
  });

  it("honours the stored scope choice", async () => {
    localStorage.setItem(STORAGE_KEY, "instance");
    const store = useTimelineStore();

    await store.init(true);

    expect(store.scope).toBe("instance");
    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "instance" }),
    );
  });

  it("honours a stored federated scope for anonymous visitors", async () => {
    localStorage.setItem(STORAGE_KEY, "federated");
    const store = useTimelineStore();

    await store.init(false);

    expect(store.scope).toBe("federated");
    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "federated" }),
    );
  });

  it("ignores a stored mine scope for anonymous visitors", async () => {
    localStorage.setItem(STORAGE_KEY, "mine");
    const store = useTimelineStore();

    await store.init(false);

    expect(store.scope).toBe("instance");
    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "instance" }),
    );
  });

  it("ignores invalid stored scope values", async () => {
    localStorage.setItem(STORAGE_KEY, "bogus");
    // Own activity exists so the "mine" default doesn't fall back.
    vi.mocked(timelineApi.listTimeline).mockResolvedValue(
      page([makeActivity("a1")]),
    );
    const store = useTimelineStore();

    await store.init(true);

    expect(readStoredScope()).toBeNull();
    expect(store.scope).toBe("mine");
  });

  it("persists the scope choice and reloads", async () => {
    vi.mocked(timelineApi.listTimeline).mockResolvedValue(
      page([makeActivity("a1")]),
    );
    const store = useTimelineStore();
    await store.init(true);
    vi.clearAllMocks();

    await store.setScope("instance");

    expect(store.scope).toBe("instance");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("instance");
    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "instance" }),
    );
  });

  it("reloads when the mode changes", async () => {
    const store = useTimelineStore();
    await store.init(true);
    vi.clearAllMocks();

    await store.setMode("all");

    expect(store.mode).toBe("all");
    expect(timelineApi.listTimeline).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "all" }),
    );
  });

  it("appends pages via loadMore", async () => {
    const listTimeline = vi.mocked(timelineApi.listTimeline);
    listTimeline.mockResolvedValueOnce(page([makeActivity("a1")], "cursor-1"));
    const store = useTimelineStore();
    await store.init(false);

    listTimeline.mockResolvedValueOnce(page([makeActivity("a2")]));
    await store.loadMore();

    expect(listTimeline).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "cursor-1" }),
    );
    expect(store.items.map((a) => a.id)).toEqual(["a1", "a2"]);
    expect(store.hasMore).toBe(false);
  });

  it("records the error message on failure", async () => {
    vi.mocked(timelineApi.listTimeline).mockRejectedValue(
      new Error("network down"),
    );
    const store = useTimelineStore();

    await store.init(false);

    expect(store.error).toBe("network down");
    expect(store.items).toEqual([]);
  });
});
