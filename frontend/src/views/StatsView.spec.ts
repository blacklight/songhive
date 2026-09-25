import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as statsApi from "@/api/stats";
import StatsView from "./StatsView.vue";

vi.mock("@/api/stats", () => ({
  getTopStats: vi.fn(),
  getPlaysStats: vi.fn(),
  getGenresTimeline: vi.fn(),
  getReleasesStats: vi.fn(),
  getClockStats: vi.fn(),
}));

const getTopStats = vi.mocked(statsApi.getTopStats);
const getPlaysStats = vi.mocked(statsApi.getPlaysStats);
const getGenresTimeline = vi.mocked(statsApi.getGenresTimeline);
const getReleasesStats = vi.mocked(statsApi.getReleasesStats);
const getClockStats = vi.mocked(statsApi.getClockStats);

const emptyTop = { artists: [], albums: [], tracks: [], genres: [] };
const loadedTop = {
  artists: [{ name: "Artist A", play_count: 3 }],
  albums: [{ name: "Album A", play_count: 2 }],
  tracks: [{ name: "Track A", play_count: 5 }],
  genres: [{ name: "Rock", play_count: 4 }],
};
const buckets = [{ bucket: "2026-09-10", count: 2 }];
const genreBuckets = [
  { bucket: "2026-09-07", genres: [{ name: "rock", count: 1 }] },
];
const releases = [{ bucket: "1990s", count: 1 }];
const clock = Array.from({ length: 24 }, (_, hour) => ({ hour, count: 0 }));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div/>" } }],
  });
}

function mountView() {
  return mount(StatsView, {
    global: {
      plugins: [createTestRouter(), i18n],
      stubs: {
        StatsHistogram: true,
        GenreTimeline: true,
        ListeningClock: true,
      },
    },
  });
}

function mockAll(top: unknown = loadedTop) {
  getTopStats.mockResolvedValue(top as statsApi.StatsTop);
  getPlaysStats.mockResolvedValue(buckets);
  getGenresTimeline.mockResolvedValue(genreBuckets);
  getReleasesStats.mockResolvedValue(releases);
  getClockStats.mockResolvedValue(clock);
}

describe("StatsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    mockAll();
  });

  it("fires all five endpoints in parallel on mount", async () => {
    mountView();
    await flushPromises();

    for (const call of [
      getTopStats,
      getPlaysStats,
      getGenresTimeline,
      getReleasesStats,
      getClockStats,
    ]) {
      expect(call).toHaveBeenCalledTimes(1);
    }
    const periodArg = getPlaysStats.mock.calls[0][0];
    expect(periodArg?.from).toBeTruthy();
    expect(periodArg?.to).toBeTruthy();
    expect(periodArg?.tz).toBeTruthy();
    expect(periodArg?.weekStart).toBeGreaterThanOrEqual(0);
    expect(periodArg?.weekStart).toBeLessThanOrEqual(6);
    expect(getPlaysStats.mock.calls[0][1]).toBe("day");
    expect(getGenresTimeline.mock.calls[0][1]).toBe("week");
    expect(getReleasesStats.mock.calls[0][1]).toBe("decade");
  });

  it("renders the top lists when loaded", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain("Artist A");
    expect(wrapper.text()).toContain("Album A");
    expect(wrapper.text()).toContain("Track A");
    expect(wrapper.text()).toContain("Rock");
  });

  it("shows the empty state when nothing was played", async () => {
    mockAll(emptyTop);
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain("No listens recorded in this period.");
  });

  it("shows the error state and retries", async () => {
    getTopStats.mockRejectedValue(new Error("boom"));
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find(".stats-view__error").exists()).toBe(true);

    mockAll();
    await wrapper.find(".stats-view__error button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".stats-view__error").exists()).toBe(false);
    expect(wrapper.text()).toContain("Artist A");
  });

  it("refetches only the plays histogram on group change", async () => {
    const wrapper = mountView();
    await flushPromises();
    vi.clearAllMocks();

    const segments = wrapper.findAll(".stats-view__segment");
    const monthButton = segments[0]
      .findAll("button")
      .find((b) => b.text() === "Month");
    await monthButton!.trigger("click");
    await flushPromises();

    expect(getPlaysStats).toHaveBeenCalledTimes(1);
    expect(getPlaysStats.mock.calls[0][1]).toBe("month");
    expect(getTopStats).not.toHaveBeenCalled();
    expect(getGenresTimeline).not.toHaveBeenCalled();
    expect(getClockStats).not.toHaveBeenCalled();
  });

  it("refetches everything when the range changes", async () => {
    const wrapper = mountView();
    await flushPromises();
    vi.clearAllMocks();

    const input = wrapper.find('input[type="date"]');
    expect(input.exists()).toBe(true);
    await input.setValue("2026-08-01");
    await flushPromises();

    for (const call of [
      getTopStats,
      getPlaysStats,
      getGenresTimeline,
      getReleasesStats,
      getClockStats,
    ]) {
      expect(call).toHaveBeenCalledTimes(1);
    }
    expect(getPlaysStats.mock.calls[0][0]?.from).toBe(
      new Date("2026-08-01T00:00:00").toISOString(),
    );
  });

  it("passes data props to the chart components", async () => {
    const wrapper = mountView();
    await flushPromises();

    const histogram = wrapper.findComponent({ name: "StatsHistogram" });
    expect(histogram.exists()).toBe(true);
    // ISO bucket labels are rendered in the configured locale.
    expect(histogram.props("buckets")).toEqual([
      { bucket: "Sep 10, 2026", count: 2 },
    ]);

    const timeline = wrapper.findComponent({ name: "GenreTimeline" });
    expect(timeline.props("buckets")).toEqual([
      { bucket: "Sep 7, 2026", genres: [{ name: "rock", count: 1 }] },
    ]);
  });
});
