import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listTags,
  listTagItems,
  listUserTags,
  listUserTagItems,
  listTagActivities,
  deleteTag,
  addTags,
  removeTag,
  type TagSummary,
  type TaggedItem,
} from "./tags";

vi.mock("./client", async (importOriginal) => {
  const original = await importOriginal<typeof import("./client")>();
  return {
    ...original,
    apiRequest: vi.fn(),
    apiRequestWithHeaders: vi.fn(),
  };
});

const apiRequest = vi.mocked(client.apiRequest);
const apiRequestWithHeaders = vi.mocked(client.apiRequestWithHeaders);

function makeHeaders(values: Record<string, string>) {
  return new Map(Object.entries(values)) as unknown as Headers;
}

const sampleTag: TagSummary = {
  name: "rock",
  item_count: 1,
  first_used: null,
  last_used: null,
};

const sampleItem: TaggedItem = {
  type: "track",
  id: "track-1",
};

describe("tags api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequestWithHeaders.mockReset();
  });

  it("listTags fetches /tags/ with query params", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleTag],
      headers: makeHeaders({
        "X-Total-Count": "1",
        "X-List-Offset": "0",
      }),
    });

    const result = await listTags({
      q: "rock",
      limit: 10,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });

    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/tags/", {
      query: {
        q: "rock",
        limit: 10,
        offset: 0,
        sort_by: "name",
        sort_dir: "asc",
      },
    });
    expect(result.items).toEqual([sampleTag]);
    expect(result.total).toBe(1);
    expect(result.offset).toBe(0);
  });

  it("listTagItems fetches by tag name", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleItem],
      headers: makeHeaders({
        "X-Total-Count": "1",
        "X-List-Offset": "0",
      }),
    });

    const result = await listTagItems("rock", {
      limit: 10,
      offset: 0,
      type: "track",
    });

    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/tags/rock", {
      query: {
        limit: 10,
        offset: 0,
        type: "track",
      },
    });
    expect(result.items).toEqual([sampleItem]);
  });

  it("deleteTag sends DELETE to /tags/{tag}", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await deleteTag("rock");
    expect(apiRequest).toHaveBeenCalledWith("/tags/rock", {
      method: "DELETE",
    });
  });

  it("addTags posts to /{type}/{id}/tags", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await addTags("tracks", "track-1", { tags: ["rock"] });
    expect(apiRequest).toHaveBeenCalledWith("/tracks/track-1/tags", {
      method: "POST",
      body: { tags: ["rock"] },
    });
  });

  it("removeTag deletes /{type}/{id}/tags/{tag}", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await removeTag("tracks", "track-1", "rock");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/track-1/tags/rock", {
      method: "DELETE",
    });
  });

  it("listUserTags fetches /users/{id}/tags", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleTag],
      headers: makeHeaders({
        "X-Total-Count": "1",
        "X-List-Offset": "0",
      }),
    });

    const result = await listUserTags("user-1", { limit: 10 });

    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/users/user-1/tags", {
      query: { limit: 10 },
    });
    expect(result.items).toEqual([sampleTag]);
  });

  it("listUserTagItems fetches /users/{id}/tags/{tag}", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleItem],
      headers: makeHeaders({
        "X-Total-Count": "1",
        "X-List-Offset": "0",
      }),
    });

    const result = await listUserTagItems("user-1", "rock", { limit: 10 });

    expect(apiRequestWithHeaders).toHaveBeenCalledWith(
      "/users/user-1/tags/rock",
      {
        query: { limit: 10 },
      },
    );
    expect(result.items).toEqual([sampleItem]);
  });

  it("listTagActivities fetches /tags/{tag}/activities", async () => {
    apiRequest.mockResolvedValueOnce({ activities: [], next_cursor: null });
    await listTagActivities("rock", { limit: 10, cursor: "c1" });
    expect(apiRequest).toHaveBeenCalledWith("/tags/rock/activities", {
      query: { limit: 10, cursor: "c1" },
    });
  });
});
