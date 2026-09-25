import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getClockStats,
  getGenresTimeline,
  getPlaysStats,
  getReleasesStats,
  getTopStats,
} from "./stats";
import * as client from "./client";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const period = {
  from: "2026-08-01T00:00:00.000Z",
  to: "2026-08-31T23:59:59.999Z",
  tz: "Europe/Rome",
};

describe("stats api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("getTopStats passes the period and default limit", async () => {
    apiRequest.mockResolvedValueOnce({
      artists: [],
      albums: [],
      tracks: [],
      genres: [],
    });
    await getTopStats(period);
    expect(apiRequest).toHaveBeenCalledWith("/stats/top", {
      query: { ...period, limit: 10 },
    });
  });

  it("getTopStats forwards a custom limit", async () => {
    apiRequest.mockResolvedValueOnce({
      artists: [],
      albums: [],
      tracks: [],
      genres: [],
    });
    await getTopStats(period, 25);
    expect(apiRequest).toHaveBeenCalledWith("/stats/top", {
      query: { ...period, limit: 25 },
    });
  });

  it("getPlaysStats defaults to day grouping", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getPlaysStats(period);
    expect(apiRequest).toHaveBeenCalledWith("/stats/plays", {
      query: { ...period, group_by: "day", week_start: undefined },
    });
  });

  it("getPlaysStats forwards the group_by", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getPlaysStats(period, "month");
    expect(apiRequest).toHaveBeenCalledWith("/stats/plays", {
      query: { ...period, group_by: "month", week_start: undefined },
    });
  });

  it("getPlaysStats forwards the locale week start", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getPlaysStats({ ...period, weekStart: 6 });
    expect(apiRequest).toHaveBeenCalledWith("/stats/plays", {
      query: { ...period, group_by: "day", week_start: 6 },
    });
  });

  it("getGenresTimeline defaults to week grouping", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getGenresTimeline(period);
    expect(apiRequest).toHaveBeenCalledWith("/stats/genres-timeline", {
      query: { ...period, group_by: "week", week_start: undefined },
    });
  });

  it("getReleasesStats defaults to decade grouping", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getReleasesStats(period);
    expect(apiRequest).toHaveBeenCalledWith("/stats/releases", {
      query: { ...period, group_by: "decade" },
    });
  });

  it("getReleasesStats accepts year grouping", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getReleasesStats(period, "year");
    expect(apiRequest).toHaveBeenCalledWith("/stats/releases", {
      query: { ...period, group_by: "year" },
    });
  });

  it("getClockStats passes the period without group_by", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getClockStats(period);
    expect(apiRequest).toHaveBeenCalledWith("/stats/clock", {
      query: period,
    });
  });

  it("omits empty period params", async () => {
    apiRequest.mockResolvedValueOnce([]);
    await getClockStats();
    expect(apiRequest).toHaveBeenCalledWith("/stats/clock", {
      query: { from: undefined, to: undefined, tz: undefined },
    });
  });
});
