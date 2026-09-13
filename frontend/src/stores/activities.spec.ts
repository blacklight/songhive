import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import { useActivitiesStore } from "./activities";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  likeActivity: vi.fn(),
  unlikeActivity: vi.fn(),
  boostActivity: vi.fn(),
  unboostActivity: vi.fn(),
  replyToActivity: vi.fn(),
  listActivityLikes: vi.fn(),
  listActivityBoosts: vi.fn(),
  listActivityReplies: vi.fn(),
  updateActivity: vi.fn(),
  deleteActivity: vi.fn(),
}));

const listEntityActivities = vi.mocked(activitiesApi.listEntityActivities);
const likeActivity = vi.mocked(activitiesApi.likeActivity);
const unlikeActivity = vi.mocked(activitiesApi.unlikeActivity);
const boostActivity = vi.mocked(activitiesApi.boostActivity);
const unboostActivity = vi.mocked(activitiesApi.unboostActivity);
const replyToActivity = vi.mocked(activitiesApi.replyToActivity);
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
    like_count: 0,
    boost_count: 0,
    reply_count: 0,
    liked: false,
    boosted: false,
    can_interact: true,
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

  it("like bumps the like counter on the cached activity", async () => {
    likeActivity.mockResolvedValue({ status: "ok", activity_id: "l1" });
    const store = useActivitiesStore();
    const activity = createActivity("a1");
    await store.like(activity);
    expect(store.updatedActivity("a1")?.liked).toBe(true);
    expect(store.updatedActivity("a1")?.like_count).toBe(1);
  });

  it("unlike clears the liked flag and drops the counter", async () => {
    likeActivity.mockResolvedValue({ status: "ok", activity_id: "l1" });
    unlikeActivity.mockResolvedValue({ status: "ok" });
    const store = useActivitiesStore();
    const activity = createActivity("a1");
    await store.like(activity);
    await store.unlike(activity);
    expect(unlikeActivity).toHaveBeenCalledWith("a1");
    expect(store.isLiked("a1")).toBe(false);
    expect(store.updatedActivity("a1")?.liked).toBe(false);
    expect(store.updatedActivity("a1")?.like_count).toBe(0);
    // The activity can be liked again.
    await store.like(activity);
    expect(likeActivity).toHaveBeenCalledTimes(2);
    expect(store.updatedActivity("a1")?.like_count).toBe(1);
  });

  it("unlike honours a server-reported liked flag", async () => {
    unlikeActivity.mockResolvedValue({ status: "ok" });
    const store = useActivitiesStore();
    const activity = { ...createActivity("a1"), liked: true, like_count: 3 };
    await store.unlike(activity);
    expect(store.updatedActivity("a1")?.liked).toBe(false);
    expect(store.updatedActivity("a1")?.like_count).toBe(2);
  });

  it("boost marks the activity as boosted exactly once", async () => {
    boostActivity.mockResolvedValue({ status: "ok", activity_id: "b1" });
    const store = useActivitiesStore();
    const activity = createActivity("a1");
    await store.boost(activity);
    await store.boost(activity);
    expect(boostActivity).toHaveBeenCalledTimes(1);
    expect(store.isBoosted("a1")).toBe(true);
    expect(store.updatedActivity("a1")?.boost_count).toBe(1);
  });

  it("unboost clears the boosted flag and drops the counter", async () => {
    boostActivity.mockResolvedValue({ status: "ok", activity_id: "b1" });
    unboostActivity.mockResolvedValue({ status: "ok" });
    const store = useActivitiesStore();
    const activity = createActivity("a1");
    await store.boost(activity);
    await store.unboost(activity);
    expect(unboostActivity).toHaveBeenCalledWith("a1");
    expect(store.isBoosted("a1")).toBe(false);
    expect(store.updatedActivity("a1")?.boosted).toBe(false);
    expect(store.updatedActivity("a1")?.boost_count).toBe(0);
    // The activity can be boosted again.
    await store.boost(activity);
    expect(boostActivity).toHaveBeenCalledTimes(2);
    expect(store.updatedActivity("a1")?.boost_count).toBe(1);
  });

  it("retractReaction unlikes the target and removes the like card", async () => {
    unlikeActivity.mockResolvedValue({ status: "ok" });
    listEntityActivities.mockResolvedValueOnce({
      activities: [
        {
          ...createActivity("l1"),
          activity_type: "like",
          can_interact: false,
          in_reply_to_activity_id: "a1",
        },
      ],
      next_cursor: null,
    });
    const store = useActivitiesStore();
    await store.load("track", "t1");
    const reaction = store.items[0];
    const target = { ...createActivity("a1"), liked: true, like_count: 1 };
    await store.retractReaction(reaction, target);
    expect(unlikeActivity).toHaveBeenCalledWith("a1");
    expect(store.items).toEqual([]);
    expect(store.isRemoved("l1")).toBe(true);
    expect(store.updatedActivity("a1")?.liked).toBe(false);
    expect(store.updatedActivity("a1")?.like_count).toBe(0);
  });

  it("retractReaction unboosts the target and removes the boost card", async () => {
    unboostActivity.mockResolvedValue({ status: "ok" });
    const store = useActivitiesStore();
    const reaction = {
      ...createActivity("b1"),
      activity_type: "announce" as const,
      can_interact: false,
      in_reply_to_activity_id: "a1",
    };
    const target = { ...createActivity("a1"), boosted: true, boost_count: 1 };
    await store.retractReaction(reaction, target);
    expect(unboostActivity).toHaveBeenCalledWith("a1");
    expect(store.isRemoved("b1")).toBe(true);
    expect(store.updatedActivity("a1")?.boosted).toBe(false);
    expect(store.updatedActivity("a1")?.boost_count).toBe(0);
  });

  it("retractReaction falls back to the bare endpoint without a target", async () => {
    unlikeActivity.mockResolvedValue({ status: "ok" });
    const store = useActivitiesStore();
    const reaction = {
      ...createActivity("l2"),
      activity_type: "like" as const,
      can_interact: false,
      in_reply_to_activity_id: "a9",
    };
    await store.retractReaction(reaction);
    expect(unlikeActivity).toHaveBeenCalledWith("a9");
    expect(store.isRemoved("l2")).toBe(true);
  });

  it("reply posts the reply and bumps the reply counter", async () => {
    replyToActivity.mockResolvedValue({
      ...createActivity("r1"),
      activity_type: "reply",
      in_reply_to_activity_id: "a1",
    });
    const store = useActivitiesStore();
    const activity = createActivity("a1");
    const created = await store.reply(activity, { status: "hi" });
    expect(replyToActivity).toHaveBeenCalledWith("a1", { status: "hi" });
    expect(created.id).toBe("r1");
    expect(store.updatedActivity("a1")?.reply_count).toBe(1);
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
    expect(store.updatedActivity("a1")?.content).toBe("edited");
  });

  it("update caches the activity even when it is not in the feed", async () => {
    updateActivity.mockResolvedValue({
      ...createActivity("a1"),
      content: "edited",
      content_source: "edited",
    });
    const store = useActivitiesStore();
    await store.update("a1", { content: "edited" });
    expect(store.items).toEqual([]);
    expect(store.updatedActivity("a1")?.content).toBe("edited");
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
    expect(store.isRemoved("a1")).toBe(true);
    expect(store.updatedActivity("a1")).toBeUndefined();
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
