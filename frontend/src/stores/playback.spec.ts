import { describe, it, expect, vi, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { usePlaybackStore } from "./playback";
import { usePlayerStore } from "./player";
import * as playbackApi from "@/api/playback";
import type { PlaybackSessionState, QueueTrackData } from "@/api/playback";
import type { QueueTrack } from "@/player/types";

vi.mock("@/api/playback", () => ({
  getPlaybackSession: vi.fn(),
  setSessionOutputs: vi.fn(),
  sendPlaybackCommand: vi.fn(),
}));

vi.mock("@/api/ws", () => ({
  eventBus: {
    on: vi.fn(),
    off: vi.fn(),
    connect: vi.fn(),
    playbackControl: vi.fn(),
  },
}));

function makeState(queue: QueueTrackData[]): PlaybackSessionState {
  return {
    session_id: "s1",
    user_id: "u1",
    state: "playing",
    current_index: 0,
    position_seconds: 0,
    position_anchor_at: null,
    live_position_seconds: 0,
    repeat: "off",
    shuffle: false,
    volume: 1,
    controller_connection_id: null,
    queue,
    outputs: [
      {
        id: "out1",
        output_kind: "stream",
        output_stream_id: "os1",
        connection_id: null,
        status: "live",
        last_error: null,
        latency_offset_ms: null,
        name: "Icecast",
      },
    ],
    last_active_at: null,
  };
}

function makeQueueTrack(id: string): QueueTrack {
  return {
    id,
    title: `Track ${id}`,
    artist_id: `artist-${id}`,
    artist_name: `Artist ${id}`,
    album_id: `album-${id}`,
    album_title: `Album ${id}`,
    duration: 180,
    artwork_url: `/api/v1/files/${id}/download`,
    visibility: "public",
  } as QueueTrack;
}

describe("usePlaybackStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("maps enriched queue metadata onto player tracks", async () => {
    const store = usePlaybackStore();
    const playerStore = usePlayerStore();
    const state = makeState([
      {
        id: "t1",
        title: "Song",
        artist: "Artist",
        album: "Album",
        duration: 200,
        artist_id: "artist-1",
        album_id: "album-1",
        image_url: "/api/v1/files/img1/download",
        visibility: "public",
      },
    ]);
    vi.mocked(playbackApi.sendPlaybackCommand).mockResolvedValue(state);
    vi.mocked(playbackApi.setSessionOutputs).mockResolvedValue(state);

    await store.selectOutput("os1");

    const track = playerStore.currentTrack;
    expect(track?.artist_id).toBe("artist-1");
    expect(track?.album_id).toBe("album-1");
    expect(track?.album_title).toBe("Album");
    expect(track?.artist_name).toBe("Artist");
    expect(track?.artwork_url).toBe("/api/v1/files/img1/download");
  });

  it("round-trips display metadata through set_queue commands", async () => {
    const store = usePlaybackStore();
    const playerStore = usePlayerStore();
    store.registerWithPlayer();
    playerStore.setSessionMode(true);
    const state = makeState([]);
    vi.mocked(playbackApi.sendPlaybackCommand).mockResolvedValue(state);

    playerStore.playTrack(makeQueueTrack("t1"));
    await Promise.resolve();

    const calls = vi.mocked(playbackApi.sendPlaybackCommand).mock.calls;
    const setQueueCall = calls.find(([body]) => body.command === "set_queue");
    expect(setQueueCall).toBeDefined();
    const queue = setQueueCall![0].args?.queue as QueueTrackData[];
    expect(queue[0]).toMatchObject({
      id: "t1",
      artist_id: "artist-t1",
      album_id: "album-t1",
      image_url: "/api/v1/files/t1/download",
      visibility: "public",
    });
  });

  it("applies the session volume to the player store", async () => {
    const store = usePlaybackStore();
    const playerStore = usePlayerStore();
    const state = { ...makeState([]), volume: 0.6 };
    vi.mocked(playbackApi.setSessionOutputs).mockResolvedValue(state);

    await store.selectOutput("os1");

    expect(playerStore.volume).toBe(0.6);
  });

  it("setVolume sends throttled set_volume commands in session mode", async () => {
    vi.useFakeTimers();
    try {
      const store = usePlaybackStore();
      const playerStore = usePlayerStore();
      store.registerWithPlayer();
      playerStore.setSessionMode(true);
      vi.mocked(playbackApi.sendPlaybackCommand).mockResolvedValue(
        makeState([]),
      );

      playerStore.setVolume(0.3);
      expect(playerStore.volume).toBe(0.3);
      expect(playbackApi.sendPlaybackCommand).toHaveBeenCalledTimes(1);
      expect(
        vi.mocked(playbackApi.sendPlaybackCommand).mock.calls[0][0],
      ).toMatchObject({ command: "set_volume", args: { volume: 0.3 } });

      // Rapid slider ticks within the window coalesce into one trailing send.
      playerStore.setVolume(0.5);
      playerStore.setVolume(0.6);
      expect(playbackApi.sendPlaybackCommand).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(300);
      expect(playbackApi.sendPlaybackCommand).toHaveBeenCalledTimes(2);
      expect(
        vi.mocked(playbackApi.sendPlaybackCommand).mock.calls[1][0],
      ).toMatchObject({ command: "set_volume", args: { volume: 0.6 } });
    } finally {
      vi.useRealTimers();
    }
  });

  it("toggleMute maps to volume 0 on the output in session mode", async () => {
    const store = usePlaybackStore();
    const playerStore = usePlayerStore();
    store.registerWithPlayer();
    playerStore.setSessionMode(true);
    vi.mocked(playbackApi.sendPlaybackCommand).mockResolvedValue(makeState([]));

    playerStore.toggleMute();
    await Promise.resolve();
    expect(
      vi.mocked(playbackApi.sendPlaybackCommand).mock.calls[0][0],
    ).toMatchObject({ command: "set_volume", args: { volume: 0 } });
  });
});
