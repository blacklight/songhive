import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import * as podcastsApi from "@/api/podcasts";
import PodcastSyncTab from "./PodcastSyncTab.vue";

vi.mock("@/api/podcasts", () => ({
  getPodcastSyncConfig: vi.fn(),
  putPodcastSyncConfig: vi.fn(),
  deletePodcastSyncConfig: vi.fn(),
  syncPodcastsNow: vi.fn(),
}));

const storedConfig = {
  server_type: "gpodder" as const,
  server_url: "https://gpodder.example.com",
  username: "alice",
  device_id: "songhive",
  mode: "pull" as const,
  enabled: true,
  has_password: true,
  last_synced_at: "2025-10-01T12:00:00Z",
  last_error: null,
};

function mountTab() {
  return mount(PodcastSyncTab);
}

describe("PodcastSyncTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows an empty form when no sync is configured", async () => {
    vi.mocked(podcastsApi.getPodcastSyncConfig).mockResolvedValue(null);
    const wrapper = mountTab();
    await flushPromises();

    const urlInput = wrapper.find('input[type="url"]');
    expect(urlInput.exists()).toBe(true);
    expect(wrapper.text()).toContain("GPodder sync");
    // No config yet → no sync-now/delete actions.
    expect(wrapper.text()).not.toContain("Sync now");
  });

  it("populates the form from an existing config", async () => {
    vi.mocked(podcastsApi.getPodcastSyncConfig).mockResolvedValue(storedConfig);
    const wrapper = mountTab();
    await flushPromises();

    const urlInput = wrapper.find('input[type="url"]');
    expect((urlInput.element as HTMLInputElement).value).toBe(
      "https://gpodder.example.com",
    );
    const text = wrapper.text();
    expect(text).toContain("Sync now");
    expect(text).toContain("Last synced");
  });

  it("saves the config without sending a blank password", async () => {
    vi.mocked(podcastsApi.getPodcastSyncConfig).mockResolvedValue(null);
    vi.mocked(podcastsApi.putPodcastSyncConfig).mockResolvedValue(storedConfig);
    const wrapper = mountTab();
    await flushPromises();

    await wrapper.find('input[type="url"]').setValue("https://gpodder.net");
    const inputs = wrapper.findAll('input[type="text"], input:not([type])');
    await inputs[0].setValue("bob");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(podcastsApi.putPodcastSyncConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        server_type: "gpodder",
        server_url: "https://gpodder.net",
        username: "bob",
        mode: "pull",
      }),
    );
    expect(
      vi.mocked(podcastsApi.putPodcastSyncConfig).mock.calls[0][0],
    ).not.toHaveProperty("password");
  });

  it("hides the device field for Nextcloud servers", async () => {
    vi.mocked(podcastsApi.getPodcastSyncConfig).mockResolvedValue(null);
    const wrapper = mountTab();
    await flushPromises();

    // gpodder.net-compatible default → device id is shown.
    expect(wrapper.text()).toContain("Device ID");

    const selects = wrapper.findAll("select");
    await selects[0].setValue("nextcloud");
    await flushPromises();

    expect(wrapper.text()).not.toContain("Device ID");
    // The Nextcloud-specific server URL hint replaces the gpodder one.
    expect(wrapper.text()).toContain("must be installed");
  });

  it("runs an immediate sync and shows the result", async () => {
    vi.mocked(podcastsApi.getPodcastSyncConfig).mockResolvedValue(storedConfig);
    vi.mocked(podcastsApi.syncPodcastsNow).mockResolvedValue({
      subscribed: 2,
      unsubscribed: 1,
      pushed_adds: 0,
      pushed_removes: 0,
      errors: [],
    });
    const wrapper = mountTab();
    await flushPromises();

    const syncButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Sync now"));
    expect(syncButton).toBeDefined();
    await syncButton!.trigger("click");
    await flushPromises();

    expect(podcastsApi.syncPodcastsNow).toHaveBeenCalled();
    expect(wrapper.text()).toContain("Sync finished");
  });

  it("deletes the configuration", async () => {
    vi.mocked(podcastsApi.getPodcastSyncConfig).mockResolvedValue(storedConfig);
    vi.mocked(podcastsApi.deletePodcastSyncConfig).mockResolvedValue(undefined);
    const wrapper = mountTab();
    await flushPromises();

    const deleteButton = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Remove sync"));
    await deleteButton!.trigger("click");
    await flushPromises();

    expect(podcastsApi.deletePodcastSyncConfig).toHaveBeenCalled();
    expect(wrapper.text()).not.toContain("Last synced");
  });
});
