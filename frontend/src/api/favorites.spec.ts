import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listFavorites,
  addFavorite,
  removeFavorite,
  type FavoriteResponse,
} from "./favorites";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleFavorite: FavoriteResponse = {
  id: "f1",
  track_id: "t1",
  created_at: "2026-01-01T00:00:00Z",
};

describe("favorites api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listFavorites fetches the favorites endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleFavorite]);
    const result = await listFavorites();
    expect(apiRequest).toHaveBeenCalledWith("/favorites", { query: undefined });
    expect(result).toEqual([sampleFavorite]);
  });

  it("listFavorites passes pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listFavorites({ limit: 10, offset: 5 });
    expect(apiRequest).toHaveBeenCalledWith("/favorites", {
      query: { limit: 10, offset: 5 },
    });
  });

  it("addFavorite posts by track id", async () => {
    apiRequest.mockResolvedValueOnce(sampleFavorite);
    const result = await addFavorite("t1");
    expect(apiRequest).toHaveBeenCalledWith("/favorites/t1", {
      method: "POST",
    });
    expect(result).toEqual(sampleFavorite);
  });

  it("removeFavorite sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await removeFavorite("t1");
    expect(apiRequest).toHaveBeenCalledWith("/favorites/t1", {
      method: "DELETE",
    });
  });
});
