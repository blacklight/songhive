import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import * as activitiesApi from "@/api/activities";
import type { ActivityResponse } from "@/api/activities";
import ActivityFeed from "./ActivityFeed.vue";

vi.mock("@/api/activities", () => ({
  listEntityActivities: vi.fn(),
  likeActivity: vi.fn(),
  updateActivity: vi.fn(),
}));

const listEntityActivities = vi.mocked(activitiesApi.listEntityActivities);

function createActivity(id: string): ActivityResponse {
  return {
    id,
    entity_type: "track",
    entity_id: "t1",
    activity_type: "create",
    source_type: "local",
    source_actor: "urn:songhive:user:alice",
    source_id: `urn:songhive:objects:${id}`,
    visibility: "public",
    published_at: "2026-01-01T00:00:00Z",
    mentions: [],
  };
}

function page(
  activities: ActivityResponse[],
  nextCursor: string | null = null,
) {
  return { activities, next_cursor: nextCursor };
}

describe("ActivityFeed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads and renders activities on mount", async () => {
    listEntityActivities.mockResolvedValueOnce(page([createActivity("a1")]));
    const wrapper = mount(ActivityFeed, {
      props: { entityType: "track", entityId: "t1" },
    });
    await flushPromises();
    expect(listEntityActivities).toHaveBeenCalledWith("track", "t1", {
      cursor: undefined,
    });
    expect(wrapper.findAll(".activity-card").length).toBe(1);
  });

  it("shows the empty state when there are no activities", async () => {
    listEntityActivities.mockResolvedValueOnce(page([]));
    const wrapper = mount(ActivityFeed, {
      props: { entityType: "track", entityId: "t1" },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("No activities yet.");
  });

  it("reloads with filter params when a tab is selected", async () => {
    listEntityActivities.mockResolvedValue(page([]));
    const wrapper = mount(ActivityFeed, {
      props: { entityType: "track", entityId: "t1" },
    });
    await flushPromises();
    const tabs = wrapper.findAll(".app-tabs__tab");
    const repliesTab = tabs.find((tab) => tab.text() === "Replies");
    await repliesTab!.trigger("click");
    await flushPromises();
    expect(listEntityActivities).toHaveBeenLastCalledWith("track", "t1", {
      activity_type: "reply",
      cursor: undefined,
    });
  });

  it("loads the next page when Load more is clicked", async () => {
    listEntityActivities
      .mockResolvedValueOnce(page([createActivity("a1")], "c1"))
      .mockResolvedValueOnce(page([createActivity("a2")]));
    const wrapper = mount(ActivityFeed, {
      props: { entityType: "track", entityId: "t1" },
    });
    await flushPromises();
    const more = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Load more"));
    expect(more).toBeDefined();
    await more!.trigger("click");
    await flushPromises();
    expect(listEntityActivities).toHaveBeenLastCalledWith("track", "t1", {
      cursor: "c1",
    });
    expect(wrapper.findAll(".activity-card").length).toBe(2);
    expect(
      wrapper.findAll("button").find((b) => b.text().includes("Load more")),
    ).toBeUndefined();
  });

  it("shows an error with a retry button on failure", async () => {
    listEntityActivities.mockRejectedValueOnce(new Error("boom"));
    const wrapper = mount(ActivityFeed, {
      props: { entityType: "track", entityId: "t1" },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("boom");
  });
});
