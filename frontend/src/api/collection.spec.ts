import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listCollection,
  addToCollection,
  removeFromCollection,
  type CollectionItemResponse,
} from "./collection";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleItem: CollectionItemResponse = {
  id: "c1",
  item_type: "library",
  item_id: "lib-1",
  created_at: "2026-01-01T00:00:00Z",
};

describe("collection api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listCollection fetches the collection endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleItem]);
    const result = await listCollection();
    expect(apiRequest).toHaveBeenCalledWith("/collection/", {
      query: undefined,
    });
    expect(result).toEqual([sampleItem]);
  });

  it("listCollection passes filter and pagination params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listCollection({ item_type: "playlist", limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/collection/", {
      query: { item_type: "playlist", limit: 10, offset: 5 },
    });
  });

  it("addToCollection posts by item type and id", async () => {
    apiRequest.mockResolvedValueOnce(sampleItem);
    const result = await addToCollection("library", "lib-1");
    expect(apiRequest).toHaveBeenCalledWith("/collection/library/lib-1", {
      method: "POST",
    });
    expect(result).toEqual(sampleItem);
  });

  it("removeFromCollection sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await removeFromCollection("track", "t1");
    expect(apiRequest).toHaveBeenCalledWith("/collection/track/t1", {
      method: "DELETE",
    });
  });
});
