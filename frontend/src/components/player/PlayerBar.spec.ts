import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";
import { usePlayerStore } from "@/stores/player";
import PlayerBar from "./PlayerBar.vue";
import type { QueueTrack } from "@/player/types";

function makeTrack(id: string, title = `Track ${id}`): QueueTrack {
  return {
    id,
    title,
    artist_id: `artist-${id}`,
    artist_name: `Artist ${id}`,
    album_id: `album-${id}`,
    album_title: `Album ${id}`,
    duration: 180,
    artwork_url: `https://example.com/${id}.jpg`,
    visibility: "public",
  };
}

function createMockEngine() {
  return {
    load: vi.fn(),
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setNextTrack: vi.fn(),
    destroy: vi.fn(),
  };
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/albums/:id", name: "album", component: { template: "<div/>" } },
    ],
  });
}

async function mountPlayerBar() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createTestRouter();
  const store = usePlayerStore();
  const engine = createMockEngine();
  store.registerEngine(engine);

  const wrapper = mount(PlayerBar, {
    global: {
      plugins: [pinia, router],
    },
    attachTo: document.body,
  });

  return { wrapper, store, engine };
}

describe("PlayerBar", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("does not render when no track is loaded", () => {
    setActivePinia(createPinia());
    const wrapper = mount(PlayerBar, {
      global: {
        plugins: [createTestRouter()],
      },
    });

    expect(wrapper.find(".player-bar").exists()).toBe(false);
  });

  it("renders full layout when a track is loaded", async () => {
    const { wrapper, store } = await mountPlayerBar();
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 0);
    await flushPromises();

    expect(wrapper.find(".player-bar").exists()).toBe(true);
    expect(wrapper.find(".player-bar__full").exists()).toBe(true);
    expect(wrapper.find(".now-playing").exists()).toBe(true);
    expect(wrapper.text()).toContain(tracks[0].title);
  });

  it("Play/Pause button toggles playback state", async () => {
    const { wrapper, store } = await mountPlayerBar();
    store.playAll([makeTrack("a"), makeTrack("b")], 0);
    await flushPromises();

    const playButton = wrapper.find(".player-bar__full .player-controls__play");
    expect(playButton.exists()).toBe(true);

    await playButton.trigger("click");
    expect(store.isPlaying).toBe(false);

    await playButton.trigger("click");
    expect(store.isPlaying).toBe(true);
  });

  it("Next button advances to the next track", async () => {
    const { wrapper, store } = await mountPlayerBar();
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 0);
    await flushPromises();

    const nextButton = wrapper.find(".player-bar__full .player-controls__next");
    expect(nextButton.exists()).toBe(true);

    await nextButton.trigger("click");
    expect(store.index).toBe(1);
    expect(store.currentTrack?.id).toBe("b");
  });

  it("toggles the QueuePanel", async () => {
    const { wrapper, store } = await mountPlayerBar();
    store.playAll([makeTrack("a"), makeTrack("b")], 0);
    await flushPromises();

    const toggle = wrapper.find(".player-bar__full .player-bar__queue-toggle");
    expect(wrapper.find(".queue-panel").exists()).toBe(false);

    await toggle.trigger("click");
    await flushPromises();
    expect(wrapper.find(".queue-panel").exists()).toBe(true);

    await toggle.trigger("click");
    await flushPromises();
    expect(wrapper.find(".queue-panel").exists()).toBe(false);
  });

  it("ProgressBar calls seek on range input change", async () => {
    const { wrapper, store, engine } = await mountPlayerBar();
    store.playAll([makeTrack("a")], 0);
    store.updateDuration(180);
    store.updateTime(0);
    await flushPromises();

    const slider = wrapper.find(
      '.player-bar__full .progress-bar input[type="range"]',
    );
    expect(slider.exists()).toBe(true);

    await slider.setValue(50);
    await flushPromises();

    expect(store.currentTime).toBe(50);
    expect(engine.seek).toHaveBeenCalledWith(50);
  });

  it("mini layout elements are present and hidden by default", async () => {
    const { wrapper, store } = await mountPlayerBar();
    store.playAll([makeTrack("a")], 0);
    await flushPromises();

    const full = wrapper.find(".player-bar__full");
    const mini = wrapper.find(".player-bar__mini");

    expect(full.exists()).toBe(true);
    expect(mini.exists()).toBe(true);
    expect(mini.find(".player-bar__mini-info").exists()).toBe(true);
    expect(mini.find(".player-bar__mini-play").exists()).toBe(true);
    expect(mini.find(".player-bar__mini-next").exists()).toBe(true);
  });

  it("expanded layout can be toggled on mobile", async () => {
    const { wrapper, store } = await mountPlayerBar();
    store.playAll([makeTrack("a")], 0);
    await flushPromises();

    const expandButton = wrapper.find(".player-bar__mini-info");
    expect(expandButton.exists()).toBe(true);

    await expandButton.trigger("click");
    await flushPromises();

    expect(wrapper.classes()).toContain("player-bar--expanded");
    expect(wrapper.find(".player-bar__expanded").exists()).toBe(true);
  });
});
