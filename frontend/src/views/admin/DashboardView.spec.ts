import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import { ApiError } from "@/api/client";
import * as adminApi from "@/api/admin";
import type { AdminStats } from "@/api/admin";
import DashboardView from "./DashboardView.vue";

vi.mock("@/api/admin", () => ({
  getStats: vi.fn(),
}));

function createStats(): AdminStats {
  return {
    storage: { total_files: 100, total_size_bytes: 1024 * 1024 * 5 },
    users: {
      total_users: 10,
      active_users: 8,
      users_by_role: {},
      recent_registrations: 1,
    },
    content: {
      total_tracks: 50,
      total_albums: 5,
      total_playlists: 2,
      total_libraries: 3,
    },
    federation: {
      enabled: true,
      instance_domain: "example.com",
      instance_name: "Songhive",
    },
  };
}

describe("DashboardView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(adminApi.getStats).mockResolvedValue(createStats());
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
  });

  it("renders stats from the API", async () => {
    wrapper = mount(DashboardView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.getStats).toHaveBeenCalled();
    expect(wrapper.text()).toContain("100");
    expect(wrapper.text()).toContain("50");
    expect(wrapper.text()).toContain("10");
    expect(wrapper.text()).toContain("5.00 MB");
  });

  it("refreshes stats when the refresh button is clicked", async () => {
    wrapper = mount(DashboardView, { global: { plugins: [i18n] } });
    await flushPromises();

    vi.mocked(adminApi.getStats).mockClear();
    vi.mocked(adminApi.getStats).mockResolvedValueOnce(createStats());

    const refreshButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.dashboard.refresh"));
    expect(refreshButton).toBeDefined();
    await refreshButton?.trigger("click");
    await flushPromises();

    expect(adminApi.getStats).toHaveBeenCalledTimes(1);
  });

  it("auto-refreshes every 60 seconds", async () => {
    wrapper = mount(DashboardView, { global: { plugins: [i18n] } });
    await flushPromises();

    vi.mocked(adminApi.getStats).mockClear();
    vi.mocked(adminApi.getStats).mockResolvedValue(createStats());

    vi.advanceTimersByTime(60000);
    await flushPromises();

    expect(adminApi.getStats).toHaveBeenCalledTimes(1);
  });

  it("shows an error toast when stats fail to load", async () => {
    vi.mocked(adminApi.getStats).mockRejectedValueOnce(
      new ApiError("Server error", 500, { detail: "network" }),
    );

    wrapper = mount(DashboardView, { global: { plugins: [i18n] } });
    await flushPromises();

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("error");
    expect(toastStore.toasts[0].message).toContain("network");
  });
});
