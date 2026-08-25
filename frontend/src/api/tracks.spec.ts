import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import {
  listTracks,
  getTrack,
  updateTrack,
  deleteTrack,
  deleteTracks,
  type TrackResponse,
  type TrackUpdate,
} from "./tracks";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleTrack: TrackResponse = {
  id: "t1",
  title: "Test Track",
  artist_id: "a1",
  album_id: "al1",
  track_number: 1,
  disc_number: 1,
  duration: 120,
  genre: "Rock",
  audio_url: "/api/v1/stream/t1",
  owner_id: "u1",
  visibility: "public",
};

describe("tracks api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("listTracks fetches the tracks endpoint", async () => {
    apiRequest.mockResolvedValueOnce([sampleTrack]);
    const result = await listTracks();
    expect(apiRequest).toHaveBeenCalledWith("/tracks/", { query: undefined });
    expect(result).toEqual([sampleTrack]);
  });

  it("listTracks passes filter and pagination query params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await listTracks({
      q: "foo",
      artist_id: "a1",
      album_id: "al1",
      genre: "Rock",
      year_from: 2000,
      year_to: 2020,
      library_id: "lib1",
      limit: 10,
      offset: 5,
    });
    expect(apiRequest).toHaveBeenCalledWith("/tracks/", {
      query: {
        q: "foo",
        artist_id: "a1",
        album_id: "al1",
        genre: "Rock",
        year_from: 2000,
        year_to: 2020,
        library_id: "lib1",
        limit: 10,
        offset: 5,
      },
    });
  });

  it("getTrack fetches by id", async () => {
    apiRequest.mockResolvedValueOnce(sampleTrack);
    const result = await getTrack("t1");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1");
    expect(result).toEqual(sampleTrack);
  });

  it("updateTrack patches with the provided body", async () => {
    apiRequest.mockResolvedValueOnce(sampleTrack);
    const body: TrackUpdate = { title: "New Title", visibility: "local" };
    await updateTrack("t1", body);
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1", {
      method: "PATCH",
      body,
    });
  });

  it("deleteTrack sends a DELETE request", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await deleteTrack("t1");
    expect(apiRequest).toHaveBeenCalledWith("/tracks/t1", {
      method: "DELETE",
    });
  });

  it("deleteTracks sends a single DELETE request for multiple ids", async () => {
    const response = { deleted: 2, track_ids: ["t1", "t2"] };
    apiRequest.mockResolvedValueOnce(response);
    const result = await deleteTracks(["t1", "t2"]);
    expect(apiRequest).toHaveBeenCalledWith("/tracks/bulk", {
      method: "DELETE",
      body: { track_ids: ["t1", "t2"] },
    });
    expect(result).toEqual(response);
  });
});
