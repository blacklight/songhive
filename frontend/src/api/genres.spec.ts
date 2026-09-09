import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listGenres,
  listGenreItems,
  deleteGenre,
  addGenres,
  removeGenre,
  type GenreSummary,
  type GenreItem,
} from "./genres";

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

const sampleGenre: GenreSummary = {
  name: "rock",
  item_count: 1,
  first_used: null,
  last_used: null,
};

const sampleItem: GenreItem = {
  type: "track",
  id: "track-1",
};

describe("genres api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
    apiRequestWithHeaders.mockReset();
  });

  it("listGenres fetches /genres/ with query params", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleGenre],
      headers: makeHeaders({
        "X-Total-Count": "1",
        "X-List-Offset": "0",
      }),
    });

    const result = await listGenres({
      q: "rock",
      limit: 10,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });

    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/genres/", {
      query: {
        q: "rock",
        limit: 10,
        offset: 0,
        sort_by: "name",
        sort_dir: "asc",
      },
    });
    expect(result.items).toEqual([sampleGenre]);
    expect(result.total).toBe(1);
    expect(result.offset).toBe(0);
  });

  it("listGenreItems fetches by genre name", async () => {
    apiRequestWithHeaders.mockResolvedValueOnce({
      body: [sampleItem],
      headers: makeHeaders({
        "X-Total-Count": "1",
        "X-List-Offset": "0",
      }),
    });

    const result = await listGenreItems("rock", {
      limit: 10,
      offset: 0,
      type: "track",
    });

    expect(apiRequestWithHeaders).toHaveBeenCalledWith("/genres/rock", {
      query: {
        limit: 10,
        offset: 0,
        type: "track",
      },
    });
    expect(result.items).toEqual([sampleItem]);
  });

  it("deleteGenre sends DELETE to /genres/{genre}", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await deleteGenre("rock");
    expect(apiRequest).toHaveBeenCalledWith("/genres/rock", {
      method: "DELETE",
    });
  });

  it("addGenres posts to /{type}/{id}/genres", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await addGenres("tracks", "track-1", { genres: ["rock"] });
    expect(apiRequest).toHaveBeenCalledWith("/tracks/track-1/genres", {
      method: "POST",
      body: { genres: ["rock"] },
    });
  });

  it("removeGenre deletes /{type}/{id}/genres/{genre}", async () => {
    apiRequest.mockResolvedValueOnce(null);
    await removeGenre("tracks", "track-1", "rock");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/track-1/genres/rock", {
      method: "DELETE",
    });
  });
});
