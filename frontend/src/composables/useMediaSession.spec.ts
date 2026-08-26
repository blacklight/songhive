import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent, h, nextTick } from "vue";
import { usePlayerStore } from "@/stores/player";
import { useMediaSession } from "./useMediaSession";
import type { QueueTrack } from "@/player/types";

const TestComponent = defineComponent({
  setup() {
    useMediaSession();
    return () => h("div");
  },
});

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

function createMockMediaSession() {
  return {
    metadata: null as MediaMetadata | null,
    playbackState: "none" as MediaSessionPlaybackState,
    setActionHandler: vi.fn(),
  } as unknown as MediaSession;
}

describe("useMediaSession", () => {
  let mockMediaSession: MediaSession;
  let originalNavigator: Navigator;
  let originalMediaMetadata: typeof MediaMetadata | undefined;

  beforeEach(() => {
    localStorage.clear();
    mockMediaSession = createMockMediaSession();
    originalNavigator = window.navigator;
    originalMediaMetadata = window.MediaMetadata;

    Object.defineProperty(window, "MediaMetadata", {
      value: vi.fn(),
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, "navigator", {
      value: { mediaSession: mockMediaSession },
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "navigator", {
      value: originalNavigator,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, "MediaMetadata", {
      value: originalMediaMetadata,
      configurable: true,
      writable: true,
    });
  });

  it("registers all five action handlers on mount", async () => {
    setActivePinia(createPinia());

    mount(TestComponent);
    await nextTick();

    const handler = mockMediaSession.setActionHandler as ReturnType<
      typeof vi.fn
    >;
    const actions = handler.mock.calls.map((call: string[]) => call[0]);

    expect(actions).toContain("play");
    expect(actions).toContain("pause");
    expect(actions).toContain("nexttrack");
    expect(actions).toContain("previoustrack");
    expect(actions).toContain("seekto");
  });

  it("updates metadata when currentTrack changes", async () => {
    setActivePinia(createPinia());
    const store = usePlayerStore();

    mount(TestComponent);
    await nextTick();

    store.playAll([makeTrack("a", "Song A")], 0);
    await nextTick();

    expect(mockMediaSession.metadata).not.toBeNull();
  });

  it("sets playbackState based on isPlaying", async () => {
    setActivePinia(createPinia());
    const store = usePlayerStore();

    mount(TestComponent);
    await nextTick();

    store.playAll([makeTrack("a")], 0);
    await nextTick();
    expect(mockMediaSession.playbackState).toBe("playing");

    store.pause();
    await nextTick();
    expect(mockMediaSession.playbackState).toBe("paused");
  });

  it("nulls action handlers and metadata on unmount", async () => {
    setActivePinia(createPinia());

    const wrapper = mount(TestComponent);
    await nextTick();

    expect(mockMediaSession.setActionHandler).toHaveBeenCalled();
    (mockMediaSession.setActionHandler as ReturnType<typeof vi.fn>).mockClear();

    wrapper.unmount();
    await nextTick();

    const handler = mockMediaSession.setActionHandler as ReturnType<
      typeof vi.fn
    >;
    const handlers = handler.mock.calls.map((call: string[]) => call[0]);

    expect(handlers).toContain("play");
    expect(handlers).toContain("pause");
    expect(handlers).toContain("nexttrack");
    expect(handlers).toContain("previoustrack");
    expect(handlers).toContain("seekto");

    expect(mockMediaSession.metadata).toBeNull();
  });
});
