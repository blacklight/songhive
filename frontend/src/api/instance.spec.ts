import { describe, it, expect, vi, beforeEach } from "vitest";
import * as client from "./client";
import { getInstance, type InstanceInfo } from "./instance";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

const apiRequest = vi.mocked(client.apiRequest);

const sampleInstance: InstanceInfo = {
  uri: "music.example.com",
  title: "Songhive",
  description: "A federated music sharing service.",
  short_description: "A federated music sharing service.",
  email: "",
  version: "Songhive 0.0.1 (Mastodon-compatible)",
  songhive_version: "0.0.1",
  urls: { streaming_api: "" },
  stats: { user_count: 1, status_count: 0, domain_count: 0 },
  thumbnail: null,
  languages: ["en"],
  registrations: true,
  approval_required: false,
  invites_enabled: false,
  configuration: {},
  contact_account: null,
  rules: [],
};

describe("instance api", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  it("getInstance fetches /api/v1/instance", async () => {
    apiRequest.mockResolvedValueOnce(sampleInstance);
    const result = await getInstance();
    expect(apiRequest).toHaveBeenCalledWith("/instance");
    expect(result).toEqual(sampleInstance);
  });
});
