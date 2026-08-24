import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { usePlayerStore } from "./player";
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

describe("usePlayerStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("playTrack creates a single-track queue and starts playback", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const track = makeTrack("a");

    store.playTrack(track);

    expect(store.queue).toEqual([track]);
    expect(store.currentTrack).toEqual(track);
    expect(store.isPlaying).toBe(true);
    expect(engine.load).toHaveBeenCalledWith(track, 0);
    expect(engine.play).toHaveBeenCalled();
  });

  it("playTrack sets queue context and starts at the selected track", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];

    store.playTrack(tracks[1], tracks);

    expect(store.queue).toEqual(tracks);
    expect(store.index).toBe(1);
    expect(store.currentTrack).toEqual(tracks[1]);
    expect(engine.load).toHaveBeenCalledWith(tracks[1], 0);
  });

  it("playAll sets queue and starts at the given index", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];

    store.playAll(tracks, 2);

    expect(store.queue).toEqual(tracks);
    expect(store.index).toBe(2);
    expect(engine.load).toHaveBeenCalledWith(tracks[2], 0);
    expect(engine.play).toHaveBeenCalled();
  });

  it("next advances to the next track", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 0);

    engine.load.mockClear();
    engine.play.mockClear();
    store.next();

    expect(store.index).toBe(1);
    expect(engine.load).toHaveBeenCalledWith(tracks[1]);
    expect(engine.play).toHaveBeenCalled();
  });

  it("next wraps to the start when repeat is all", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b")];
    store.playAll(tracks, 1);
    store.cycleRepeat();

    engine.load.mockClear();
    engine.play.mockClear();
    store.next();

    expect(store.index).toBe(0);
    expect(engine.load).toHaveBeenCalledWith(tracks[0]);
    expect(engine.play).toHaveBeenCalled();
  });

  it("next with repeat one restarts the same track", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const track = makeTrack("a");
    store.playTrack(track);
    store.cycleRepeat();
    store.cycleRepeat();
    expect(store.repeat).toBe("one");

    engine.load.mockClear();
    engine.seek.mockClear();
    engine.play.mockClear();
    store.next();

    expect(store.index).toBe(0);
    expect(engine.seek).toHaveBeenCalledWith(0);
    expect(engine.load).not.toHaveBeenCalled();
    expect(engine.play).toHaveBeenCalled();
  });

  it("next does nothing at the last track when repeat is off", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b")];
    store.playAll(tracks, 1);

    engine.load.mockClear();
    store.next();

    expect(store.index).toBe(1);
    expect(engine.load).not.toHaveBeenCalled();
  });

  it("prev restarts the current track when currentTime > 3", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b")];
    store.playAll(tracks, 1);
    store.updateDuration(180);
    store.updateTime(10);

    engine.load.mockClear();
    store.prev();

    expect(store.index).toBe(1);
    expect(store.currentTime).toBe(0);
    expect(engine.seek).toHaveBeenCalledWith(0);
    expect(engine.load).not.toHaveBeenCalled();
  });

  it("prev goes to the previous track when near the start", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 2);
    store.updateTime(2);

    engine.load.mockClear();
    store.prev();

    expect(store.index).toBe(1);
    expect(engine.load).toHaveBeenCalledWith(tracks[1]);
  });

  it("cycleRepeat rotates through off, all, one", () => {
    const store = usePlayerStore();
    expect(store.repeat).toBe("off");
    store.cycleRepeat();
    expect(store.repeat).toBe("all");
    store.cycleRepeat();
    expect(store.repeat).toBe("one");
    store.cycleRepeat();
    expect(store.repeat).toBe("off");
  });

  it("toggleShuffle shuffles with the current track at the front", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c"), makeTrack("d"), makeTrack("e")];
    store.playAll(tracks, 2);

    store.toggleShuffle();

    expect(store.shuffle).toBe(true);
    expect(store.index).toBe(0);
    expect(store.queue.length).toBe(tracks.length);
    expect(store.queue[0]).toEqual(tracks[2]);
    expect(new Set(store.queue.map((t) => t.id))).toEqual(
      new Set(tracks.map((t) => t.id)),
    );
  });

  it("toggleShuffle off restores the original order", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c"), makeTrack("d"), makeTrack("e")];
    store.playAll(tracks, 2);

    store.toggleShuffle();
    store.toggleShuffle();

    expect(store.shuffle).toBe(false);
    expect(store.queue).toEqual(tracks);
    expect(store.index).toBe(2);
  });

  it("enqueueNext inserts after the current index", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 1);
    const extra = makeTrack("x");

    store.enqueueNext(extra);

    expect(store.queue.map((t) => t.id)).toEqual(["a", "b", "x", "c"]);
    expect(store.index).toBe(1);
  });

  it("enqueueNext keeps position in originalQueue when shuffle is active", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 1);
    const extra = makeTrack("x");

    store.toggleShuffle();
    store.enqueueNext(extra);
    store.toggleShuffle();

    expect(store.queue.map((t) => t.id)).toEqual(["a", "b", "x", "c"]);
    expect(store.index).toBe(1);
  });

  it("removeAt decrements index when removing an earlier track", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 1);

    store.removeAt(0);

    expect(store.queue.map((t) => t.id)).toEqual(["b", "c"]);
    expect(store.index).toBe(0);
  });

  it("removeAt at the current index loads the next track", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 0);

    engine.load.mockClear();
    store.removeAt(0);

    expect(store.queue.map((t) => t.id)).toEqual(["b", "c"]);
    expect(store.index).toBe(0);
    expect(engine.load).toHaveBeenCalledWith(tracks[1]);
  });

  it("removeAt after the current index does not affect index", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    store.playAll(tracks, 0);

    store.removeAt(2);

    expect(store.queue.map((t) => t.id)).toEqual(["a", "b"]);
    expect(store.index).toBe(0);
  });

  it("clear resets state", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    store.playAll([makeTrack("a"), makeTrack("b")], 0);

    store.clear();

    expect(store.queue).toEqual([]);
    expect(store.index).toBe(-1);
    expect(store.currentTrack).toBeNull();
    expect(engine.destroy).toHaveBeenCalled();
  });

  it("seek clamps currentTime and calls the engine", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);
    store.playTrack(makeTrack("a"));
    store.updateDuration(100);

    store.seek(50);
    expect(store.currentTime).toBe(50);
    expect(engine.seek).toHaveBeenCalledWith(50);

    store.seek(200);
    expect(store.currentTime).toBe(100);
    expect(engine.seek).toHaveBeenLastCalledWith(100);

    store.seek(-5);
    expect(store.currentTime).toBe(0);
    expect(engine.seek).toHaveBeenLastCalledWith(0);
  });

  it("setVolume and toggleMute update state and the engine", () => {
    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);

    store.setVolume(0.5);
    expect(store.volume).toBe(0.5);
    expect(engine.setVolume).toHaveBeenCalledWith(0.5, false);

    store.toggleMute();
    expect(store.muted).toBe(true);
    expect(engine.setVolume).toHaveBeenLastCalledWith(0.5, true);
  });

  it("persists state to localStorage and restores it", async () => {
    const firstStore = usePlayerStore();
    const engine = createMockEngine();
    firstStore.registerEngine(engine);
    const tracks = [makeTrack("a"), makeTrack("b"), makeTrack("c")];
    firstStore.playAll(tracks, 1);
    firstStore.setVolume(0.5);
    firstStore.toggleShuffle();
    firstStore.cycleRepeat();
    firstStore.updateTime(42);

    await flushPromises();
    vi.advanceTimersByTime(1100);

    const persistedQueue = JSON.parse(
      localStorage.getItem("songhive.player.queue")!,
    ) as QueueTrack[];
    expect(persistedQueue).toEqual(firstStore.queue);
    expect(localStorage.getItem("songhive.player.index")).toBe(
      String(firstStore.index),
    );
    expect(localStorage.getItem("songhive.player.position")).toBe("42");
    expect(localStorage.getItem("songhive.player.shuffle")).toBe("true");
    expect(localStorage.getItem("songhive.player.repeat")).toBe("all");
    expect(localStorage.getItem("songhive.player.volume")).toBe("0.5");

    setActivePinia(createPinia());
    const secondStore = usePlayerStore();

    expect(secondStore.queue).toEqual(firstStore.queue);
    expect(secondStore.index).toBe(firstStore.index);
    expect(secondStore.restoredPosition).toBe(42);
    expect(secondStore.shuffle).toBe(true);
    expect(secondStore.repeat).toBe("all");
    expect(secondStore.volume).toBe(0.5);
  });

  it("debounces position persistence writes", async () => {
    const store = usePlayerStore();
    store.playTrack(makeTrack("a"));

    for (let i = 1; i <= 10; i++) {
      store.updateTime(i);
      await flushPromises();
    }
    vi.advanceTimersByTime(200);

    expect(localStorage.getItem("songhive.player.position")).toBeNull();

    vi.advanceTimersByTime(1000);

    expect(localStorage.getItem("songhive.player.position")).toBe("10");
  });

  it("restoredPosition is consumed by playAll", () => {
    localStorage.setItem("songhive.player.queue", JSON.stringify([makeTrack("a"), makeTrack("b")]));
    localStorage.setItem("songhive.player.index", "1");
    localStorage.setItem("songhive.player.position", "33");

    const store = usePlayerStore();
    const engine = createMockEngine();
    store.registerEngine(engine);

    expect(store.restoredPosition).toBe(33);

    store.playAll(store.queue, 1);

    expect(engine.load).toHaveBeenCalledWith(store.queue[1], 33);
    expect(store.restoredPosition).toBeNull();
  });
});
