import { describe, it, expect, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useOutputsStore } from "./outputs";
import * as outputsApi from "@/api/outputs";
import type { OutputResponse, ProviderResponse } from "@/api/outputs";

vi.mock("@/api/outputs", () => ({
  listOutputProviders: vi.fn(),
  listOutputs: vi.fn(),
  createOutput: vi.fn(),
  getOutput: vi.fn(),
  updateOutput: vi.fn(),
  deleteOutput: vi.fn(),
  validateOutput: vi.fn(),
}));

const fakeProvider: ProviderResponse = {
  provider_type: "icecast",
  user_configurable: true,
  can_create: true,
  fields: [
    { name: "host", type: "text", required: true, label: "Host" },
    { name: "password", type: "password", required: true, label: "Password" },
  ],
};

const fakeOutput: OutputResponse = {
  id: "o1",
  user_id: "u1",
  provider_type: "icecast",
  name: "Studio stream",
  config: { host: "icecast.example.com" },
  capabilities: null,
  enabled: true,
  last_error: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

describe("useOutputsStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("loads providers and outputs", async () => {
    const store = useOutputsStore();
    vi.mocked(outputsApi.listOutputProviders).mockResolvedValue([fakeProvider]);
    vi.mocked(outputsApi.listOutputs).mockResolvedValue([fakeOutput]);

    await store.loadProviders();
    await store.loadOutputs();

    expect(store.providers).toEqual([fakeProvider]);
    expect(store.outputs).toEqual([fakeOutput]);
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("exposes a synthetic web output", () => {
    const store = useOutputsStore();
    expect(store.webOutput.id).toBe("web");
    expect(store.webOutput.provider_type).toBe("web");
    expect(store.webOutput.enabled).toBe(true);
  });

  it("creates an output and refreshes the list", async () => {
    const store = useOutputsStore();
    vi.mocked(outputsApi.createOutput).mockResolvedValue(fakeOutput);

    const created = await store.createOutput({
      provider_type: "icecast",
      name: "Studio stream",
      config: { host: "icecast.example.com" },
    });

    expect(created).toEqual(fakeOutput);
    expect(store.outputs).toEqual([fakeOutput]);
  });

  it("deletes an output and removes it from the list", async () => {
    const store = useOutputsStore();
    store.outputs = [fakeOutput];
    vi.mocked(outputsApi.deleteOutput).mockResolvedValue(undefined);

    await store.deleteOutput("o1");

    expect(store.outputs).toEqual([]);
  });

  it("surfaces load errors", async () => {
    const store = useOutputsStore();
    vi.mocked(outputsApi.listOutputs).mockRejectedValue(
      new Error("network down"),
    );

    await store.loadOutputs();

    expect(store.outputs).toEqual([]);
    expect(store.error).toBe("network down");
    expect(store.loading).toBe(false);
  });
});
