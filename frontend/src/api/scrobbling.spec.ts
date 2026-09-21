import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  connectScrobbler,
  disconnectScrobbler,
  getScrobbleStatus,
  reportNowPlaying,
  updateScrobbleSettings,
} from "./scrobbling";
import * as client from "./client";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

describe("scrobbling api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("getScrobbleStatus fetches the status endpoint", async () => {
    apiRequest.mockResolvedValueOnce({ enabled: true, services: [] });
    await getScrobbleStatus();
    expect(apiRequest).toHaveBeenCalledWith("/scrobbling/");
  });

  it("connectScrobbler posts credentials", async () => {
    apiRequest.mockResolvedValueOnce({});
    await connectScrobbler({
      service: "lastfm",
      username: "alice",
      password: "pw",
    });
    expect(apiRequest).toHaveBeenCalledWith("/scrobbling/connect", {
      method: "POST",
      body: { service: "lastfm", username: "alice", password: "pw" },
    });
  });

  it("updateScrobbleSettings puts thresholds", async () => {
    apiRequest.mockResolvedValueOnce({});
    await updateScrobbleSettings({
      enabled: true,
      min_seconds: 45,
      min_percent: 50,
    });
    expect(apiRequest).toHaveBeenCalledWith("/scrobbling/", {
      method: "PUT",
      body: { enabled: true, min_seconds: 45, min_percent: 50 },
    });
  });

  it("disconnectScrobbler deletes the config", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await disconnectScrobbler();
    expect(apiRequest).toHaveBeenCalledWith("/scrobbling/", {
      method: "DELETE",
    });
  });

  it("reportNowPlaying posts to the now-playing endpoint", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    await reportNowPlaying("track-1");
    expect(apiRequest).toHaveBeenCalledWith("/scrobbling/now-playing/track-1", {
      method: "POST",
    });
  });
});
