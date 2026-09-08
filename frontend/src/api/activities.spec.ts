import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listEntityActivities,
  updateActivity,
  deleteActivity,
  likeActivity,
  type ActivityListResponse,
} from "./activities";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const samplePage: ActivityListResponse = {
  activities: [
    {
      id: "a1",
      entity_type: "track",
      entity_id: "t1",
      activity_type: "create",
      source_type: "local",
      source_actor: "https://example.com/users/alice",
      source_id: "https://example.com/users/alice/objects/o1",
      visibility: "public",
      published_at: "2026-01-01T00:00:00Z",
      mentions: [],
    },
  ],
  next_cursor: "cursor-1",
};

describe("activities api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listEntityActivities fetches the entity activities endpoint", async () => {
    apiRequest.mockResolvedValueOnce(samplePage);
    const result = await listEntityActivities("track", "t1");
    expect(apiRequest).toHaveBeenCalledWith("/track/t1/activities", {
      query: undefined,
    });
    expect(result).toEqual(samplePage);
  });

  it("listEntityActivities passes filter and cursor query params", async () => {
    apiRequest.mockResolvedValueOnce(samplePage);
    await listEntityActivities("album", "al1", {
      activity_type: "reply",
      source_type: "remote",
      cursor: "abc",
      limit: 50,
    });
    expect(apiRequest).toHaveBeenCalledWith("/album/al1/activities", {
      query: {
        activity_type: "reply",
        source_type: "remote",
        cursor: "abc",
        limit: 50,
      },
    });
  });

  it("updateActivity sends a PATCH request", async () => {
    apiRequest.mockResolvedValueOnce({ status: "ok" });
    const result = await updateActivity("a1", {
      content: "new text",
      visibility: "followers",
    });
    expect(apiRequest).toHaveBeenCalledWith("/activities/a1", {
      method: "PATCH",
      body: { content: "new text", visibility: "followers" },
    });
    expect(result).toEqual({ status: "ok" });
  });

  it("deleteActivity sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce({ status: "ok" });
    const result = await deleteActivity("a1");
    expect(apiRequest).toHaveBeenCalledWith("/activities/a1", {
      method: "DELETE",
    });
    expect(result).toEqual({ status: "ok" });
  });

  it("likeActivity posts to the like endpoint", async () => {
    apiRequest.mockResolvedValueOnce({ status: "ok", activity_id: "l1" });
    const result = await likeActivity("a1");
    expect(apiRequest).toHaveBeenCalledWith("/activities/a1/like", {
      method: "POST",
    });
    expect(result).toEqual({ status: "ok", activity_id: "l1" });
  });
});
