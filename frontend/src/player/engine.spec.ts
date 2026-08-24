import { describe, it, expect, beforeEach, vi } from "vitest";
import { PlayerEngine } from "./engine";
import type { QueueTrack } from "./types";

vi.mock("@/api/stream", () => ({
  streamUrl: (track: { id: string }) => `/stream/${track.id}`,
  setStreamTokenProvider: vi.fn(),
}));

vi.mock("@/api/history", () => ({
  addHistory: vi.fn(() => Promise.resolve()),
}));

function makeTrack(id: string): QueueTrack {
  return {
    id,
    title: `Track ${id}`,
    artist_id: `artist-${id}`,
    artist_name: `Artist ${id}`,
    visibility: "public",
  };
}

describe("PlayerEngine", () => {
  let engine: PlayerEngine;
  let primary: HTMLAudioElement;

  beforeEach(() => {
    engine = new PlayerEngine();
    primary = (engine as unknown as { primary: HTMLAudioElement }).primary;
    primary.currentTime = 0;
    (primary as unknown as { duration: number }).duration = 0;
    (primary as unknown as { paused: boolean }).paused = true;
    primary.src = "";
    primary.volume = 1;
    primary.muted = false;
    primary.play = vi.fn(() => Promise.resolve());
    primary.pause = vi.fn();
    primary.load = vi.fn();
  });

  it("load sets the primary source and starts loading", () => {
    const onStateChange = vi.fn();
    engine.init({ onStateChange });
    const track = makeTrack("a");

    engine.load(track);

    expect(primary.src).toBe("/stream/a");
    expect(primary.load).toHaveBeenCalled();
    expect(onStateChange).toHaveBeenCalledWith("loading");
  });

  it("load + play fires loading then playing", () => {
    const onStateChange = vi.fn();
    engine.init({ onStateChange });
    const track = makeTrack("a");

    engine.load(track);
    engine.play();

    expect(primary.play).toHaveBeenCalled();
    expect(onStateChange).toHaveBeenCalledWith("loading");
  });

  it("play event triggers playing state", () => {
    const onStateChange = vi.fn();
    engine.init({ onStateChange });
    primary.dispatchEvent(new Event("play"));
    expect(onStateChange).toHaveBeenCalledWith("playing");
  });

  it("pause event triggers paused state", () => {
    const onStateChange = vi.fn();
    engine.init({ onStateChange });
    primary.dispatchEvent(new Event("pause"));
    expect(onStateChange).toHaveBeenCalledWith("paused");
  });

  it("seek updates currentTime", () => {
    engine.init({});
    engine.seek(30);
    expect(primary.currentTime).toBe(30);
  });

  it("setVolume updates volume and muted", () => {
    engine.init({});
    engine.setVolume(0.5, true);
    expect(primary.volume).toBe(0.5);
    expect(primary.muted).toBe(true);
  });

  it("timeupdate reports time and preloads the next track at 80%", () => {
    const onTimeUpdate = vi.fn();
    engine.init({ onTimeUpdate });
    const current = makeTrack("a");
    const next = makeTrack("b");

    engine.load(current);
    engine.setNextTrack(next);
    (primary as unknown as { duration: number }).duration = 100;
    primary.currentTime = 82;
    primary.dispatchEvent(new Event("timeupdate"));

    expect(onTimeUpdate).toHaveBeenCalledWith(82);
    const preload = (engine as unknown as { preload: HTMLAudioElement })
      .preload;
    expect(preload.src).toBe("/stream/b");
    expect(preload.load).toHaveBeenCalled();
  });

  it("ended swaps primary and preload, then calls onEnded", () => {
    const onEnded = vi.fn();
    engine.init({ onEnded });
    const current = makeTrack("a");
    const next = makeTrack("b");

    engine.load(current);
    engine.setNextTrack(next);
    const preload = (engine as unknown as { preload: HTMLAudioElement })
      .preload;
    preload.src = "/stream/b";

    primary.dispatchEvent(new Event("ended"));

    expect(primary.src).toBe("/stream/b");
    expect(primary.currentTime).toBe(0);
    expect(preload.src).toBe("");
    expect(primary.play).not.toHaveBeenCalled();
    expect(onEnded).toHaveBeenCalled();
  });

  it("loadedmetadata reports duration", () => {
    const onDuration = vi.fn();
    engine.init({ onDuration });
    (primary as unknown as { duration: number }).duration = 123;
    primary.dispatchEvent(new Event("loadedmetadata"));
    expect(onDuration).toHaveBeenCalledWith(123);
  });

  it("error reports the error and sets error state", () => {
    const onError = vi.fn();
    const onStateChange = vi.fn();
    engine.init({ onError, onStateChange });
    (primary as unknown as { error: MediaError }).error = {
      code: 1,
    } as MediaError;
    primary.dispatchEvent(new Event("error"));
    expect(onError).toHaveBeenCalled();
    expect(onStateChange).toHaveBeenCalledWith("error");
  });

  it("waiting and stalled set loading state", () => {
    const onStateChange = vi.fn();
    engine.init({ onStateChange });
    primary.dispatchEvent(new Event("waiting"));
    expect(onStateChange).toHaveBeenCalledWith("loading");
    onStateChange.mockClear();
    primary.dispatchEvent(new Event("stalled"));
    expect(onStateChange).toHaveBeenCalledWith("loading");
  });

  it("canplay applies the requested start time", () => {
    engine.init({});
    const track = makeTrack("a");
    engine.load(track, 33);
    primary.dispatchEvent(new Event("canplay"));
    expect(primary.currentTime).toBe(33);
  });

  it("play logs non-AbortError rejections", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    (primary as unknown as { play: () => Promise<void> }).play = () =>
      Promise.reject(new Error("not allowed"));
    engine.init({});
    engine.play();
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(warnSpy).toHaveBeenCalledWith("play() failed", expect.any(Error));
    warnSpy.mockRestore();
  });

  it("setNextTrack with null clears the preload source", () => {
    engine.init({});
    const next = makeTrack("b");
    engine.setNextTrack(next);
    const preload = (engine as unknown as { preload: HTMLAudioElement })
      .preload;
    preload.src = "/stream/b";
    engine.setNextTrack(null);
    expect(preload.src).toBe("");
  });

  it("destroy clears state and removes listeners", () => {
    engine.init({});
    const track = makeTrack("a");
    engine.load(track);
    engine.destroy();
    expect(primary.src).toBe("");
    expect(primary.pause).toHaveBeenCalled();
  });
});
