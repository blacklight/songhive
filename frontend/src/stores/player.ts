import { defineStore } from "pinia";
import { computed, ref, watch, type Ref } from "vue";
import type {
  QueueTrack,
  RepeatMode,
  PlaybackState,
  EngineApi,
} from "@/player/types";

const STORAGE_QUEUE = "songhive.player.queue";
const STORAGE_INDEX = "songhive.player.index";
const STORAGE_POSITION = "songhive.player.position";
const STORAGE_SHUFFLE = "songhive.player.shuffle";
const STORAGE_REPEAT = "songhive.player.repeat";
const STORAGE_VOLUME = "songhive.player.volume";
const STORAGE_MUTED = "songhive.player.muted";

function readJson<T>(key: string): T | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    localStorage.removeItem(key);
    return null;
  }
}

function readNumber(key: string, fallback: number): number {
  const raw = localStorage.getItem(key);
  if (!raw) return fallback;
  const value = Number(raw);
  if (Number.isNaN(value)) {
    localStorage.removeItem(key);
    return fallback;
  }
  return value;
}

function readString<T extends string>(
  key: string,
  allowed: T[],
  fallback: T,
): T {
  const raw = localStorage.getItem(key);
  if (!raw) return fallback;
  if (allowed.includes(raw as T)) return raw as T;
  localStorage.removeItem(key);
  return fallback;
}

function readBoolean(key: string, fallback: boolean): boolean {
  const raw = localStorage.getItem(key);
  if (raw === null) return fallback;
  if (raw === "true") return true;
  if (raw === "false") return false;
  localStorage.removeItem(key);
  return fallback;
}

function shuffleArray<T>(items: T[]): T[] {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

export const usePlayerStore = defineStore("player", () => {
  const queue: Ref<QueueTrack[]> = ref([]);
  const originalQueue: Ref<QueueTrack[]> = ref([]);
  const index: Ref<number> = ref(-1);
  const repeat: Ref<RepeatMode> = ref("off");
  const shuffle: Ref<boolean> = ref(false);
  const isPlaying: Ref<boolean> = ref(false);
  const currentTime: Ref<number> = ref(0);
  const duration: Ref<number> = ref(0);
  const volume: Ref<number> = ref(1);
  const muted: Ref<boolean> = ref(false);
  const playbackState: Ref<PlaybackState> = ref("idle");

  const restoredQueue = readJson<QueueTrack[]>(STORAGE_QUEUE);
  if (Array.isArray(restoredQueue)) {
    queue.value = restoredQueue;
    const restoredIndex = Math.min(
      Math.max(readNumber(STORAGE_INDEX, 0), 0),
      queue.value.length - 1,
    );
    index.value = queue.value.length > 0 ? restoredIndex : -1;
  }

  const rawPosition = localStorage.getItem(STORAGE_POSITION);
  const parsedPosition =
    rawPosition === null ? NaN : Number(rawPosition);
  const restoredPosition = ref<number | null>(
    Number.isNaN(parsedPosition) ? null : parsedPosition,
  );
  shuffle.value = readBoolean(STORAGE_SHUFFLE, false);
  repeat.value = readString<RepeatMode>(
    STORAGE_REPEAT,
    ["off", "all", "one"],
    "off",
  );
  volume.value = Math.min(
    Math.max(readNumber(STORAGE_VOLUME, 1), 0),
    1,
  );
  muted.value = readBoolean(STORAGE_MUTED, false);

  const currentTrack = computed(() => queue.value[index.value] ?? null);

  const hasNext = computed(() => {
    if (!currentTrack.value) return false;
    if (repeat.value === "all" || repeat.value === "one") return true;
    return index.value < queue.value.length - 1;
  });

  const hasPrev = computed(() => {
    if (!currentTrack.value) return false;
    if (repeat.value === "all" || repeat.value === "one") return true;
    return index.value > 0;
  });

  const progress = computed(() =>
    duration.value > 0 ? currentTime.value / duration.value : 0,
  );

  const nextTrack = computed(() => {
    if (!currentTrack.value) return null;
    if (repeat.value === "one") return currentTrack.value;
    const nextIndex = index.value + 1;
    if (nextIndex < queue.value.length) return queue.value[nextIndex];
    if (repeat.value === "all") return queue.value[0];
    return null;
  });

  let engine: EngineApi | null = null;

  function registerEngine(api: EngineApi) {
    engine = api;
    engine.setVolume(volume.value, muted.value);
    engine.setNextTrack(nextTrack.value);
  }

  watch(
    nextTrack,
    (track) => {
      engine?.setNextTrack(track);
    },
    { immediate: true },
  );

  function playTrack(track: QueueTrack, queueContext?: QueueTrack[]) {
    const newQueue = queueContext && queueContext.length > 0 ? queueContext : [track];
    queue.value = newQueue;
    originalQueue.value = [];
    index.value = newQueue.findIndex((t) => t.id === track.id);
    if (index.value < 0) index.value = 0;

    const startAt =
      restoredPosition.value !== null &&
      currentTrack.value?.id === track.id
        ? restoredPosition.value
        : 0;
    restoredPosition.value = null;

    currentTime.value = 0;
    duration.value = 0;
    playbackState.value = "loading";
    isPlaying.value = true;
    engine?.load(track, startAt);
    engine?.play();
  }

  function playAll(tracks: QueueTrack[], startIndex = 0) {
    if (tracks.length === 0) return;
    queue.value = [...tracks];
    originalQueue.value = [];
    index.value = Math.min(Math.max(startIndex, 0), tracks.length - 1);

    const startAt =
      restoredPosition.value !== null &&
      currentTrack.value?.id === queue.value[index.value].id
        ? restoredPosition.value
        : 0;
    restoredPosition.value = null;

    currentTime.value = 0;
    duration.value = 0;
    playbackState.value = "loading";
    isPlaying.value = true;
    engine?.load(queue.value[index.value], startAt);
    engine?.play();
  }

  function enqueue(track: QueueTrack) {
    queue.value.push(track);
    if (originalQueue.value.length > 0) originalQueue.value.push(track);
  }

  function enqueueNext(track: QueueTrack) {
    const insertAt = Math.min(Math.max(index.value + 1, 0), queue.value.length);
    queue.value.splice(insertAt, 0, track);
    if (originalQueue.value.length > 0) {
      const origIdx = originalQueue.value.findIndex(
        (t) => t.id === currentTrack.value?.id,
      );
      const insertOrigAt =
        origIdx >= 0 ? origIdx + 1 : originalQueue.value.length;
      originalQueue.value.splice(insertOrigAt, 0, track);
    }
  }

  function removeAt(i: number) {
    if (i < 0 || i >= queue.value.length) return;
    const removed = queue.value[i];
    queue.value.splice(i, 1);
    if (originalQueue.value.length > 0) {
      const originalIdx = removed
        ? originalQueue.value.findIndex((t) => t.id === removed.id)
        : -1;
      if (originalIdx >= 0) originalQueue.value.splice(originalIdx, 1);
    }
    if (i < index.value) {
      index.value -= 1;
    } else if (i === index.value) {
      if (index.value >= queue.value.length) {
        index.value = queue.value.length - 1;
      }
      if (currentTrack.value && engine) {
        engine.load(currentTrack.value);
        engine.play();
      } else {
        currentTime.value = 0;
        duration.value = 0;
        playbackState.value = "idle";
        isPlaying.value = false;
      }
    }
  }

  function clear() {
    queue.value = [];
    originalQueue.value = [];
    index.value = -1;
    currentTime.value = 0;
    duration.value = 0;
    playbackState.value = "idle";
    isPlaying.value = false;
    engine?.destroy();
  }

  function play() {
    if (!currentTrack.value) return;
    isPlaying.value = true;
    engine?.play();
  }

  function pause() {
    isPlaying.value = false;
    engine?.pause();
  }

  function next() {
    if (!currentTrack.value) return;
    if (repeat.value === "one") {
      currentTime.value = 0;
      engine?.seek(0);
      play();
      return;
    }
    const nextIndex = index.value + 1;
    if (nextIndex < queue.value.length) {
      index.value = nextIndex;
    } else if (repeat.value === "all" && queue.value.length > 0) {
      index.value = 0;
    } else {
      return;
    }
    currentTime.value = 0;
    duration.value = 0;
    playbackState.value = "loading";
    isPlaying.value = true;
    const track = currentTrack.value;
    if (track) engine?.load(track);
    engine?.play();
  }

  function prev() {
    if (!currentTrack.value) return;
    if (currentTime.value > 3) {
      currentTime.value = 0;
      engine?.seek(0);
      return;
    }
    let prevIndex = index.value - 1;
    if (prevIndex < 0) {
      if (repeat.value === "all") {
        prevIndex = queue.value.length - 1;
      } else {
        return;
      }
    }
    index.value = prevIndex;
    currentTime.value = 0;
    duration.value = 0;
    playbackState.value = "loading";
    isPlaying.value = true;
    const track = currentTrack.value;
    if (track) engine?.load(track);
    engine?.play();
  }

  function seek(seconds: number) {
    const clamped = Math.min(Math.max(seconds, 0), duration.value || 0);
    currentTime.value = clamped;
    engine?.seek(clamped);
  }

  function setVolume(v: number) {
    volume.value = Math.min(Math.max(v, 0), 1);
    engine?.setVolume(volume.value, muted.value);
  }

  function toggleMute() {
    muted.value = !muted.value;
    engine?.setVolume(volume.value, muted.value);
  }

  function toggleShuffle() {
    if (shuffle.value) {
      // Disable: restore original order.
      if (originalQueue.value.length > 0) {
        const current = currentTrack.value;
        queue.value = [...originalQueue.value];
        if (current) {
          const newIndex = queue.value.findIndex((t) => t.id === current.id);
          index.value = newIndex >= 0 ? newIndex : 0;
        }
      }
      originalQueue.value = [];
      shuffle.value = false;
    } else {
      // Enable: preserve original, then shuffle with current at front.
      const current = currentTrack.value;
      originalQueue.value = [...queue.value];
      const others = queue.value.filter((t) => t.id !== current?.id);
      const shuffled = current ? [current, ...shuffleArray(others)] : shuffleArray(queue.value);
      queue.value = shuffled;
      index.value = 0;
      shuffle.value = true;
    }
  }

  function cycleRepeat() {
    if (repeat.value === "off") repeat.value = "all";
    else if (repeat.value === "all") repeat.value = "one";
    else repeat.value = "off";
  }

  function updateTime(t: number) {
    currentTime.value = t;
  }

  function updateDuration(d: number) {
    duration.value = d;
  }

  function setPlaybackState(s: PlaybackState) {
    playbackState.value = s;
    if (s === "playing") isPlaying.value = true;
    else if (s === "paused" || s === "idle" || s === "error")
      isPlaying.value = false;
  }

  // Updates the displayed time while the user is dragging the seek slider,
  // without committing a seek to the engine.
  function setDisplayedTime(seconds: number) {
    currentTime.value = Math.min(Math.max(seconds, 0), duration.value || 0);
  }

  // Persistence: debounce localStorage writes to at most once per second.
  let persistTimer: ReturnType<typeof setTimeout> | null = null;
  function persist() {
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = setTimeout(() => {
      persistTimer = null;
      localStorage.setItem(STORAGE_QUEUE, JSON.stringify(queue.value));
      localStorage.setItem(STORAGE_INDEX, String(index.value));
      localStorage.setItem(STORAGE_POSITION, String(currentTime.value));
      localStorage.setItem(STORAGE_SHUFFLE, String(shuffle.value));
      localStorage.setItem(STORAGE_REPEAT, repeat.value);
      localStorage.setItem(STORAGE_VOLUME, String(volume.value));
      localStorage.setItem(STORAGE_MUTED, String(muted.value));
    }, 1000);
  }

  watch(
    [
      queue,
      index,
      currentTime,
      shuffle,
      repeat,
      volume,
      muted,
    ],
    () => {
      persist();
    },
    { deep: true },
  );

  return {
    queue,
    originalQueue,
    index,
    repeat,
    shuffle,
    isPlaying,
    currentTime,
    duration,
    volume,
    muted,
    playbackState,
    restoredPosition,
    currentTrack,
    hasNext,
    hasPrev,
    progress,
    nextTrack,
    registerEngine,
    playTrack,
    playAll,
    enqueue,
    enqueueNext,
    removeAt,
    clear,
    play,
    pause,
    next,
    prev,
    seek,
    setVolume,
    toggleMute,
    toggleShuffle,
    cycleRepeat,
    updateTime,
    updateDuration,
    setPlaybackState,
    setDisplayedTime,
  };
});
