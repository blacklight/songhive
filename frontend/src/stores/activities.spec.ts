import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { useActivitiesStore } from "./activities";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  likeActivity: vi.fn(),
  updateActivity: vi.fn(),
  deleteActivity: vi.fn(),
}));

const listEntityActivities = vi.mocked(activitiesApi.listEntityActivities);
const likeActivity = vi.mocked(activitiesApi.likeActivity);
const updateActivity = vi.mocked(activitiesApi.updateActivity);
const deleteActivity = vi.mocked(activitiesApi.deleteActivity);

function createActivity(id: string): ActivityResponse {
  return {
    id,
    entity_type: "track",
    entity_id: "t1",
    activity_type: "create",
    source_type: "local",
    source_actor: "https://example.com/users/alice",
    source_id: `https://example.com/users/alice/objects/${id}`,
    visibility: "public",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
  };
}

describe("useActivitiesStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("load fetches the first page for an entity", async () => {
    listEntityActivities.mockResolvedValueOnce({
      activities: [createActivity("a1")],
      next_cursor: "c1",
    });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    expect(listEntityActivities).toHaveBeenCalledWith("track", "t1", {
      cursor: undefined,
    });
    expect(store.items.map((a) => a.id)).toEqual(["a1"]);
    expect(store.hasMore).toBe(true);
  });

  it("loadMore appends the next cursor page", async () => {
    listEntityActivities
      .mockResolvedValueOnce({
        activities: [createActivity("a1")],
        next_cursor: "c1",
      })
      .mockResolvedValueOnce({
        activities: [createActivity("a2")],
        next_cursor: null,
      });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    await store.loadMore();
    expect(listEntityActivities).toHaveBeenLastCalledWith("track", "t1", {
      cursor: "c1",
    });
    expect(store.items.map((a) => a.id)).toEqual(["a1", "a2"]);
    expect(store.hasMore).toBe(false);
  });

  it("setFilter maps filters to query params and reloads", async () => {
    listEntityActivities.mockResolvedValue({
      activities: [],
      next_cursor: null,
    });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    await store.setFilter("replies");
    expect(listEntityActivities).toHaveBeenLastCalledWith("track", "t1", {
      activity_type: "reply",
      cursor: undefined,
    });
    await store.setFilter("remote");
    expect(listEntityActivities).toHaveBeenLastCalledWith("track", "t1", {
      source_type: "remote",
      cursor: undefined,
    });
  });

  it("like marks the activity as liked exactly once", async () => {
    likeActivity.mockResolvedValue({ status: "ok", activity_id: "l1" });
    const store = useActivitiesStore();
    const activity = createActivity("a1");
    await store.like(activity);
    await store.like(activity);
    expect(likeActivity).toHaveBeenCalledTimes(1);
    expect(store.isLiked("a1")).toBe(true);
  });

  it("update patches the activity and updates the cached item", async () => {
    updateActivity.mockResolvedValue({
      ...createActivity("a1"),
      content: "edited",
      content_source: "edited",
      visibility: "followers",
    });
    listEntityActivities.mockResolvedValueOnce({
      activities: [createActivity("a1")],
      next_cursor: null,
    });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    await store.update("a1", { content: "edited", visibility: "followers" });
    expect(updateActivity).toHaveBeenCalledWith("a1", {
      content: "edited",
      visibility: "followers",
    });
    expect(store.items[0].content).toBe("edited");
    expect(store.items[0].visibility).toBe("followers");
  });

  it("remove deletes the activity and drops it from the feed", async () => {
    deleteActivity.mockResolvedValue({ status: "ok" });
    listEntityActivities.mockResolvedValueOnce({
      activities: [createActivity("a1"), createActivity("a2")],
      next_cursor: null,
    });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    await store.remove("a1");
    expect(deleteActivity).toHaveBeenCalledWith("a1");
    expect(store.items.map((a) => a.id)).toEqual(["a2"]);
    expect(store.isDeleting("a1")).toBe(false);
  });

  it("remove propagates API errors and keeps the item", async () => {
    deleteActivity.mockRejectedValue(new Error("boom"));
    listEntityActivities.mockResolvedValueOnce({
      activities: [createActivity("a1")],
      next_cursor: null,
    });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    await expect(store.remove("a1")).rejects.toThrow("boom");
    expect(store.items.map((a) => a.id)).toEqual(["a1"]);
  });

  it("load stores the error message on failure", async () => {
    listEntityActivities.mockRejectedValueOnce(new Error("boom"));
    const store = useActivitiesStore();
    await store.load("track", "t1");
    expect(store.error).toBe("boom");
    expect(store.items).toEqual([]);
  });
});
