import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { ApiError } from "@/api/client";
import * as instanceApi from "@/api/instance";
import { useInstanceStore } from "./instance";

vi.mock("@/api/instance", () => ({
  getInstance: vi.fn(),
}));

function makeInstance(
  overrides: Partial<instanceApi.InstanceInfo> = {},
): instanceApi.InstanceInfo {
  return {
    uri: "test.example.com",
    title: "Test Hive",
    description: "A test instance.",
    short_description: "A test instance.",
    email: "",
    version: "Songhive 1.2.3 (Mastodon-compatible)",
    songhive_version: "1.2.3",
    urls: { streaming_api: "" },
    stats: { user_count: 0, status_count: 0, domain_count: 0 },
    thumbnail: null,
    languages: ["en"],
    registrations: true,
    approval_required: false,
    invites_enabled: false,
    configuration: {},
    contact_account: null,
    rules: [],
    ...overrides,
  } as instanceApi.InstanceInfo;
}

describe("useInstanceStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("loads instance metadata and exposes registration flags", async () => {
    const store = useInstanceStore();
    vi.mocked(instanceApi.getInstance).mockResolvedValue(
      makeInstance({ registrations: true }),
    );

    await store.load();

    expect(store.registrations).toBe(true);
    expect(store.approvalRequired).toBe(false);
    expect(store.invitesEnabled).toBe(false);
    expect(store.status).toBe("loaded");
  });

  it("exposes registration settings from the response", async () => {
    const store = useInstanceStore();
    vi.mocked(instanceApi.getInstance).mockResolvedValue(
      makeInstance({
        registrations: true,
        approval_required: true,
        invites_enabled: true,
      }),
    );

    await store.load();

    expect(store.registrations).toBe(true);
    expect(store.approvalRequired).toBe(true);
    expect(store.invitesEnabled).toBe(true);
  });

  it("defaults registrations to false and records an error on failure", async () => {
    const store = useInstanceStore();
    vi.mocked(instanceApi.getInstance).mockRejectedValue(
      new ApiError("Service unavailable", 503, {
        detail: "Service unavailable",
      }),
    );

    await store.load();

    expect(store.registrations).toBe(false);
    expect(store.status).toBe("error");
    expect(store.error).toBe("Service unavailable");
  });

  it("returns a cached result on subsequent loads", async () => {
    const store = useInstanceStore();
    vi.mocked(instanceApi.getInstance).mockResolvedValue(makeInstance());

    await store.load();
    await store.load();

    expect(instanceApi.getInstance).toHaveBeenCalledTimes(1);
  });
});
