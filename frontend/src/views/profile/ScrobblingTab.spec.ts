import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import * as scrobblingApi from "@/api/scrobbling";
import ScrobblingTab from "./ScrobblingTab.vue";

vi.mock("@/api/scrobbling", () => ({
  getScrobbleStatus: vi.fn(),
  connectScrobbler: vi.fn(),
  updateScrobbleSettings: vi.fn(),
  disconnectScrobbler: vi.fn(),
  // The player engine singleton imports this transitively.
  reportNowPlaying: vi.fn(() => Promise.resolve()),
}));

const emptyStatus = {
  enabled: true,
  services: [
    { id: "lastfm", name: "Last.fm", available: true },
    { id: "librefm", name: "Libre.fm", available: false },
  ],
  thresholds: { min_seconds: 30, min_percent: null },
  config: null,
};

const storedConfig = {
  service: "lastfm",
  username: "alice",
  enabled: true,
  min_seconds: 30,
  min_percent: 25,
  last_scrobbled_at: "2025-10-01T12:00:00Z",
  last_error: null,
};

function mountTab() {
  return mount(ScrobblingTab);
}

describe("ScrobblingTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows the connect form when nothing is configured", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue(emptyStatus);
    const wrapper = mountTab();
    await flushPromises();

    expect(wrapper.text()).toContain("Scrobbling");
    expect(wrapper.text()).toContain("Last.fm");
    expect(wrapper.find('input[type="password"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Connect account");
  });

  it("marks unavailable services in the selector", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue(emptyStatus);
    const wrapper = mountTab();
    await flushPromises();

    expect(wrapper.text()).toContain("not configured on this instance");
  });

  it("shows the disabled notice when the feature is off", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue({
      ...emptyStatus,
      enabled: false,
    });
    const wrapper = mountTab();
    await flushPromises();

    expect(wrapper.text()).toContain("disabled on this instance");
    expect(wrapper.find("form").exists()).toBe(false);
  });

  it("connects an account", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue(emptyStatus);
    vi.mocked(scrobblingApi.connectScrobbler).mockResolvedValue(storedConfig);
    const wrapper = mountTab();
    await flushPromises();

    const textInputs = wrapper.findAll('input[type="text"], input:not([type])');
    await textInputs[0].setValue("alice");
    await wrapper.find('input[type="password"]').setValue("pw");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(scrobblingApi.connectScrobbler).toHaveBeenCalledWith({
      service: "lastfm",
      username: "alice",
      password: "pw",
    });
  });

  it("shows the connected state and saves thresholds", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue({
      ...emptyStatus,
      config: storedConfig,
    });
    vi.mocked(scrobblingApi.updateScrobbleSettings).mockResolvedValue(
      storedConfig,
    );
    const wrapper = mountTab();
    await flushPromises();

    expect(wrapper.text()).toContain("alice");
    expect(wrapper.text()).toContain("Last scrobbled");

    const numbers = wrapper.findAll('input[type="number"]');
    expect(numbers.length).toBe(2);
    await numbers[0].setValue(45);
    await numbers[1].setValue(50);
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(scrobblingApi.updateScrobbleSettings).toHaveBeenCalledWith({
      enabled: true,
      min_seconds: 45,
      min_percent: 50,
    });
  });

  it("disconnects the account", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue({
      ...emptyStatus,
      config: storedConfig,
    });
    vi.mocked(scrobblingApi.disconnectScrobbler).mockResolvedValue(undefined);
    const wrapper = mountTab();
    await flushPromises();

    const button = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Disconnect"));
    await button!.trigger("click");
    await flushPromises();

    expect(scrobblingApi.disconnectScrobbler).toHaveBeenCalled();
    expect(wrapper.text()).toContain("Connect account");
  });

  it("shows the last error from the config", async () => {
    vi.mocked(scrobblingApi.getScrobbleStatus).mockResolvedValue({
      ...emptyStatus,
      config: { ...storedConfig, last_error: "invalid session" },
    });
    const wrapper = mountTab();
    await flushPromises();

    expect(wrapper.text()).toContain("invalid session");
  });
});
