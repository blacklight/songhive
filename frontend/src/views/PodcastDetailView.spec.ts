import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as podcastsApi from "@/api/podcasts";
import type { PodcastEpisodeResponse, PodcastResponse } from "@/api/podcasts";
import PodcastDetailView from "./PodcastDetailView.vue";

vi.mock("@/api/podcasts", () => ({
  getPodcast: vi.fn(),
  listEpisodes: vi.fn(() => Promise.resolve([])),
  markEpisodePlayed: vi.fn(() => Promise.resolve()),
  markEpisodeUnplayed: vi.fn(() => Promise.resolve()),
  refreshPodcast: vi.fn(),
  unfollowPodcast: vi.fn(),
  episodeToQueueTrack: vi.fn((episode: PodcastEpisodeResponse) => ({
    id: episode.id,
  })),
}));

const getPodcast = vi.mocked(podcastsApi.getPodcast);
const listEpisodes = vi.mocked(podcastsApi.listEpisodes);
const markPlayed = vi.mocked(podcastsApi.markEpisodePlayed);
const markUnplayed = vi.mocked(podcastsApi.markEpisodeUnplayed);

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/podcasts",
        name: "podcasts",
        component: { template: "<div/>" },
      },
      {
        path: "/podcasts/:id",
        name: "podcast",
        component: PodcastDetailView,
      },
    ],
  });
}

function createPodcast(
  overrides: Partial<PodcastResponse> = {},
): PodcastResponse {
  return {
    id: "p1",
    feed_url: "https://example.com/feed.xml",
    title: "My Podcast",
    description: null,
    author: null,
    link: null,
    image_url: null,
    language: null,
    categories: [],
    explicit: false,
    episode_count: 2,
    unplayed_count: 1,
    latest_episode_at: null,
    last_fetched_at: null,
    last_error: null,
    following: true,
    ...overrides,
  };
}

function createEpisode(
  id: string,
  overrides: Partial<PodcastEpisodeResponse> = {},
): PodcastEpisodeResponse {
  return {
    id,
    podcast_id: "p1",
    guid: `guid-${id}`,
    title: `Episode ${id}`,
    description: null,
    link: null,
    audio_url: `https://example.com/${id}.mp3`,
    audio_type: "audio/mpeg",
    image_url: null,
    published_at: "2026-01-10T00:00:00Z",
    duration: 600,
    season_number: null,
    episode_number: null,
    episode_type: null,
    played: false,
    ...overrides,
  };
}

async function mountView() {
  const router = createTestRouter();
  await router.push("/podcasts/p1");
  const wrapper = mount(PodcastDetailView, {
    global: { plugins: [router] },
  });
  await flushPromises();
  return { wrapper, router };
}

describe("PodcastDetailView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    getPodcast.mockResolvedValue(createPodcast());
    listEpisodes.mockResolvedValue([
      createEpisode("e1"),
      createEpisode("e2", { played: true }),
    ]);
  });

  it("marks an unplayed episode as played", async () => {
    const { wrapper } = await mountView();

    const rows = wrapper.findAll(".podcast-view__episode");
    expect(rows).toHaveLength(2);
    expect(rows[0].classes()).not.toContain("podcast-view__episode--played");
    expect(rows[1].classes()).toContain("podcast-view__episode--played");

    await rows[0].find(".podcast-view__episode-played").trigger("click");
    await flushPromises();

    expect(markPlayed).toHaveBeenCalledWith("e1");
    expect(markUnplayed).not.toHaveBeenCalled();
    expect(rows[0].classes()).toContain("podcast-view__episode--played");
  });

  it("marks a played episode as unplayed", async () => {
    const { wrapper } = await mountView();

    const rows = wrapper.findAll(".podcast-view__episode");
    await rows[1].find(".podcast-view__episode-played").trigger("click");
    await flushPromises();

    expect(markUnplayed).toHaveBeenCalledWith("e2");
    expect(markPlayed).not.toHaveBeenCalled();
    expect(rows[1].classes()).not.toContain("podcast-view__episode--played");
  });
});
