import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as podcastsApi from "@/api/podcasts";
import type { PodcastResponse } from "@/api/podcasts";
import SearchBar from "@/components/ui/SearchBar.vue";
import SortControl from "@/components/ui/SortControl.vue";
import PodcastsView from "./PodcastsView.vue";

vi.mock("@/api/podcasts", () => ({
  listPodcasts: vi.fn(() => Promise.resolve([])),
  followPodcast: vi.fn(),
  importOpml: vi.fn(),
  opmlExportUrl: vi.fn(() => "/api/v1/podcasts/opml"),
}));

const listPodcasts = vi.mocked(podcastsApi.listPodcasts);

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div/>" } },
      {
        path: "/podcasts",
        name: "podcasts",
        component: PodcastsView,
      },
      {
        path: "/podcasts/:id",
        name: "podcast",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createPodcast(
  id: string,
  overrides: Partial<PodcastResponse> = {},
): PodcastResponse {
  return {
    id,
    feed_url: `https://example.com/${id}.xml`,
    title: `Podcast ${id}`,
    description: null,
    author: null,
    link: null,
    image_url: null,
    language: null,
    categories: [],
    explicit: false,
    episode_count: 0,
    unplayed_count: 0,
    latest_episode_at: null,
    last_fetched_at: null,
    last_error: null,
    following: true,
    ...overrides,
  };
}

async function mountView() {
  const router = createTestRouter();
  await router.push("/podcasts");
  const wrapper = mount(PodcastsView, {
    global: { plugins: [router] },
  });
  await flushPromises();
  return { wrapper, router };
}

describe("PodcastsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    listPodcasts.mockReset();
    listPodcasts.mockResolvedValue([]);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads podcasts sorted by latest episode descending by default", async () => {
    await mountView();
    expect(listPodcasts).toHaveBeenCalledWith(
      expect.objectContaining({ sort_by: "latest", sort_dir: "desc" }),
    );
  });

  it("renders podcast cards with unplayed badge and latest-episode tooltip", async () => {
    listPodcasts.mockResolvedValue([
      createPodcast("a", {
        title: "Alpha",
        unplayed_count: 3,
        episode_count: 10,
        latest_episode_at: "2026-01-15T12:00:00Z",
      }),
      createPodcast("b", { title: "Beta", unplayed_count: 0 }),
    ]);
    const { wrapper } = await mountView();

    const cards = wrapper.findAll(".podcasts-view__podcast");
    expect(cards).toHaveLength(2);

    const badge = cards[0].find(".podcasts-view__unplayed");
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe("3");
    expect(cards[1].find(".podcasts-view__unplayed").exists()).toBe(false);

    const expected = i18n.global.t("pages.podcasts.latestEpisodeAt", {
      date: new Date("2026-01-15T12:00:00Z").toLocaleDateString(),
    });
    expect(cards[0].attributes("title")).toBe(expected);
    expect(cards[0].find(".podcasts-view__latest").text()).toBe(expected);
    expect(cards[1].attributes("title")).toBeUndefined();
  });

  it("passes the search query to listPodcasts", async () => {
    const { wrapper } = await mountView();

    wrapper.findComponent(SearchBar).vm.$emit("update:modelValue", "chapo");
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(listPodcasts).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "chapo", offset: 0 }),
    );
  });

  it("re-sorts when the sort control changes", async () => {
    const { wrapper } = await mountView();

    const sort = wrapper.findComponent(SortControl);
    sort.vm.$emit("update:modelValue", "unplayed");
    await flushPromises();

    expect(listPodcasts).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sort_by: "unplayed",
        sort_dir: "desc",
      }),
    );

    sort.vm.$emit("update:modelValue", "name");
    await flushPromises();

    expect(listPodcasts).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort_by: "name", sort_dir: "asc" }),
    );

    sort.vm.$emit("update:direction", "desc");
    await flushPromises();

    expect(listPodcasts).toHaveBeenLastCalledWith(
      expect.objectContaining({ sort_by: "name", sort_dir: "desc" }),
    );
  });

  it("shows a search-specific empty state when nothing matches", async () => {
    const { wrapper } = await mountView();

    wrapper.findComponent(SearchBar).vm.$emit("update:modelValue", "zzz");
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(wrapper.find(".podcasts-view__empty").text()).toContain("zzz");
  });
});
