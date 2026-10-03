import { defineStore } from "pinia";
import { computed, ref, type Ref } from "vue";
import { getApiErrorMessage } from "@/api/client";
import { eventBus, type WsEvent } from "@/api/ws";
import * as playbackApi from "@/api/playback";
import type {
  CommandRequest,
  OutputSelectionRequest,
  PlaybackSessionState,
  QueueTrackData,
} from "@/api/playback";
import { usePlayerStore } from "@/stores/player";
import type { QueueTrack, RepeatMode } from "@/player/types";
import { i18n } from "@/i18n";

const WS_EVENT = "playback_session";

// Volume slider drags emit one update per tick; coalesce them into at most
// one session command per window (leading + trailing edge).
const VOLUME_THROTTLE_MS = 250;

let connectionId = "";

function getConnectionId(): string {
  if (!connectionId) {
    connectionId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }
  return connectionId;
}

function trackDataToQueueTrack(track: QueueTrackData): QueueTrack {
  return {
    id: track.id,
    title: track.title,
    artist_name: track.artist || "",
    artist_id: track.artist_id || "",
    album_id: track.album_id || undefined,
    album_title: track.album,
    duration: track.duration ?? undefined,
    artwork_url: track.image_url ?? undefined,
    visibility: track.visibility || "public",
    remote: track.remote,
    remote_url: track.remote_url,
    stream_url: track.stream_url,
    podcast_episode_id: track.podcast_episode_id,
    podcast_id: track.podcast_id,
  } as unknown as QueueTrack;
}

function queueTrackToTrackData(track: QueueTrack): QueueTrackData {
  return {
    id: track.id,
    title: track.title,
    artist: track.artist_name,
    album: track.album_title,
    duration: track.duration ?? null,
    artist_id: track.artist_id || undefined,
    album_id: track.album_id || undefined,
    image_url: track.artwork_url,
    visibility: track.visibility,
    remote: track.remote,
    remote_url: track.remote_url,
    stream_url: track.stream_url,
    podcast_episode_id: track.podcast_episode_id,
    podcast_id: track.podcast_id,
  };
}

export const usePlaybackStore = defineStore("playback", () => {
  const playerStore = usePlayerStore();

  const session: Ref<PlaybackSessionState | null> = ref(null);
  const activeOutputId: Ref<string | null> = ref(null);
  const loading = ref(false);
  const error: Ref<string | null> = ref(null);
  const handlerRegistered = ref(false);

  const isSessionMode = computed(
    () => activeOutputId.value !== null && activeOutputId.value !== "web",
  );

  let volumeTimer: ReturnType<typeof setTimeout> | null = null;
  let pendingVolume: number | null = null;
  let volumeDirtyUntil = 0;

  const currentOutput = computed(() => {
    if (!session.value) return null;
    return (
      session.value.outputs.find(
        (o) => o.output_stream_id === activeOutputId.value,
      ) || null
    );
  });

  function errorMessage(err: unknown): string {
    return (
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : i18n.global.t("errors.unknown"))
    );
  }

  function syncToPlayer(state: PlaybackSessionState): void {
    const queue = (state.queue || []).map(trackDataToQueueTrack);
    const index = Math.min(Math.max(state.current_index, 0), queue.length - 1);
    const current = queue[index];
    playerStore.setSessionState(
      queue,
      index,
      state.repeat,
      state.shuffle,
      state.live_position_seconds,
      state.state === "playing",
      state.state,
      current?.duration ?? 0,
    );
    playerStore.setSessionMode(isSessionMode.value);
    // Skip self-echoes while a local volume change is in flight so the slider
    // does not jerk mid-drag; remote controllers' values apply once the
    // window expires.
    if (Date.now() >= volumeDirtyUntil) {
      playerStore.setSessionVolume(state.volume ?? 1);
    }
  }

  function sendVolume(volume: number): void {
    volumeDirtyUntil = Date.now() + VOLUME_THROTTLE_MS * 2;
    if (volumeTimer !== null) {
      pendingVolume = volume;
      return;
    }
    volumeTimer = setTimeout(() => {
      volumeTimer = null;
      if (pendingVolume !== null) {
        const v = pendingVolume;
        pendingVolume = null;
        sendVolume(v);
      }
    }, VOLUME_THROTTLE_MS);
    void sendCommand("set_volume", { volume });
  }

  async function refreshSession(): Promise<void> {
    try {
      const state = await playbackApi.getPlaybackSession();
      session.value = state;
      syncToPlayer(state);
    } catch (err) {
      error.value = errorMessage(err);
    }
  }

  async function sendCommand(
    command: string,
    args: Record<string, unknown> = {},
  ): Promise<void> {
    const body: CommandRequest = {
      command,
      args,
      connection_id: getConnectionId(),
    };
    loading.value = true;
    try {
      const state = await playbackApi.sendPlaybackCommand(body);
      session.value = state;
      syncToPlayer(state);
      error.value = null;
    } catch (err) {
      error.value = errorMessage(err);
    } finally {
      loading.value = false;
    }
  }

  // Queue edits compose on the last in-flight edit rather than the last
  // synced session state: a second mutation issued before the first
  // response lands must not resurrect rows the earlier one dropped.
  let pendingQueue: QueueTrackData[] | null = null;

  function sessionQueue(): QueueTrackData[] {
    return pendingQueue ?? session.value?.queue ?? [];
  }

  function applyQueue(next: QueueTrackData[]): Promise<void> {
    pendingQueue = next;
    return sendCommand("set_queue", { queue: next }).finally(() => {
      if (pendingQueue === next) pendingQueue = null;
    });
  }

  async function setQueueAndPlay(
    queue: QueueTrack[],
    startIndex = 0,
  ): Promise<void> {
    const mapped = queue.map(queueTrackToTrackData);
    // A brand-new queue context supersedes any pending incremental edit.
    pendingQueue = mapped;
    await sendCommand("set_queue", { queue: mapped }).finally(() => {
      if (pendingQueue === mapped) pendingQueue = null;
    });
    if (startIndex !== 0 || session.value?.current_index !== 0) {
      await sendCommand("play_at", { index: startIndex });
    }
    await sendCommand("play");
  }

  async function selectOutput(outputId: string): Promise<void> {
    await connect();
    loading.value = true;
    error.value = null;
    try {
      const body: OutputSelectionRequest = {
        output_ids: [outputId],
        connection_id: getConnectionId(),
      };
      const state = await playbackApi.setSessionOutputs(body);
      session.value = state;
      activeOutputId.value = outputId;
      syncToPlayer(state);
    } catch (err) {
      error.value = errorMessage(err);
    } finally {
      loading.value = false;
    }
  }

  function onWsEvent(event: WsEvent): void {
    if (event.type !== WS_EVENT || !event.data) return;
    const state = event.data as PlaybackSessionState;
    session.value = state;
    syncToPlayer(state);
  }

  async function connect(): Promise<void> {
    if (!handlerRegistered.value) {
      eventBus.on(WS_EVENT, onWsEvent);
      handlerRegistered.value = true;
    }
    const connectionId = getConnectionId();
    eventBus.connect();
    eventBus.playbackControl(connectionId);
    await sendCommand("take_control");
  }

  function disconnect(): void {
    if (handlerRegistered.value) {
      eventBus.off(WS_EVENT, onWsEvent);
      handlerRegistered.value = false;
    }
  }

  const sessionController = {
    playTrack: (track: QueueTrack, queueContext?: QueueTrack[]) => {
      const queue = queueContext?.length ? queueContext : [track];
      const index = queue.findIndex((t) => t.id === track.id);
      void setQueueAndPlay(queue, index >= 0 ? index : 0);
    },
    playAll: (tracks: QueueTrack[], startIndex = 0) => {
      void setQueueAndPlay(tracks, startIndex);
    },
    playAt: (index: number) => {
      // ``index`` is in displayed-queue coordinates; resolve the row by
      // identity against any in-flight queue edit so a click right after
      // a removal still plays the track the user saw.
      const target = session.value?.queue?.[index];
      const base = sessionQueue();
      const resolved = target ? base.indexOf(target) : -1;
      const at = resolved >= 0 ? resolved : index;
      void sendCommand("play_at", { index: at });
    },
    play: () => {
      void sendCommand("play");
    },
    pause: () => {
      void sendCommand("pause");
    },
    next: () => {
      void sendCommand("next");
    },
    prev: () => {
      void sendCommand("prev");
    },
    seek: (seconds: number) => {
      void sendCommand("seek", { seconds });
    },
    toggleShuffle: (shuffle: boolean) => {
      void sendCommand("set_shuffle", { shuffle });
    },
    setRepeat: (repeat: RepeatMode) => {
      void sendCommand("set_repeat", { repeat });
    },
    setVolume: (volume: number) => {
      sendVolume(volume);
    },
    enqueue: (track: QueueTrack) => {
      void applyQueue([...sessionQueue(), queueTrackToTrackData(track)]);
    },
    extendQueue: (tracks: QueueTrack[]) => {
      if (tracks.length === 0) return;
      void applyQueue([
        ...sessionQueue(),
        ...tracks.map(queueTrackToTrackData),
      ]);
    },
    enqueueNext: (track: QueueTrack) => {
      const next = [...sessionQueue()];
      // ``playerStore.index`` is in displayed-queue coordinates; when an
      // earlier edit is still in flight the pending queue has shifted, so
      // anchor the insert to the current track entry itself.
      const currentEntry = session.value?.queue?.[playerStore.index];
      const pos = currentEntry ? next.indexOf(currentEntry) : -1;
      const insertAt =
        pos >= 0
          ? pos + 1
          : Math.min(Math.max(playerStore.index + 1, 0), next.length);
      next.splice(insertAt, 0, queueTrackToTrackData(track));
      // set_queue alone: the server keeps a surviving current track
      // playing in place, so queue edits never interrupt the output.
      void applyQueue(next);
    },
    removeAt: (index: number) => {
      const displayed = session.value?.queue ?? [];
      const base = sessionQueue();
      // ``index`` is in displayed-queue coordinates — resolve the row by
      // identity in case a prior edit still in flight already shifted the
      // pending queue.
      const target = displayed[index];
      const baseIndex = target ? base.indexOf(target) : -1;
      // The clicked row is already gone from the pending queue (e.g. a
      // double-click on the same remove button) — nothing to drop.
      if (target && baseIndex < 0) return;
      const removeIndex = baseIndex >= 0 ? baseIndex : index;
      if (removeIndex < 0 || removeIndex >= base.length) return;
      const next = base.filter((_, i) => i !== removeIndex);
      const removingCurrent = index === playerStore.index;
      void (async () => {
        await applyQueue(next);
        // Removing the playing row mirrors the local engine: start
        // whatever now occupies its slot.
        if (removingCurrent && next.length > 0) {
          await sendCommand("play_at", {
            index: Math.min(removeIndex, next.length - 1),
          });
        }
      })();
    },
    clear: () => {
      // An empty set_queue also drops the session to idle; ``stop`` would
      // leave the stored queue intact and it would echo back next sync.
      void applyQueue([]);
    },
  };

  function registerWithPlayer(): void {
    playerStore.registerSessionController(sessionController);
  }

  function setActiveOutput(outputId: string | null): void {
    activeOutputId.value = outputId;
    playerStore.setSessionMode(isSessionMode.value);
  }

  function $reset(): void {
    session.value = null;
    activeOutputId.value = null;
    loading.value = false;
    error.value = null;
    pendingQueue = null;
    if (volumeTimer !== null) {
      clearTimeout(volumeTimer);
      volumeTimer = null;
    }
    pendingVolume = null;
    volumeDirtyUntil = 0;
    playerStore.setSessionMode(false);
  }

  return {
    session,
    activeOutputId,
    loading,
    error,
    isSessionMode,
    currentOutput,
    selectOutput,
    sendCommand,
    refreshSession,
    connect,
    disconnect,
    registerWithPlayer,
    setActiveOutput,
    $reset,
  };
});
