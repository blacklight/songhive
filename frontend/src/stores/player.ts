import { defineStore } from "pinia";
import { ref, computed } from "vue";

export interface Track {
  id: string;
  title: string;
  artist_id: string;
  album_id?: string;
  duration?: number;
}

export const usePlayerStore = defineStore("player", () => {
  const currentTrack = ref<Track | null>(null);
  const queue = ref<Track[]>([]);
  const isPlaying = ref(false);
  const currentTime = ref(0);
  const volume = ref(1.0);

  const hasNext = computed(() => {
    if (!currentTrack.value) return false;
    const idx = queue.value.findIndex((t) => t.id === currentTrack.value!.id);
    return idx < queue.value.length - 1;
  });

  function play(track: Track) {
    currentTrack.value = track;
    isPlaying.value = true;
  }

  function pause() {
    isPlaying.value = false;
  }

  function next() {
    if (!currentTrack.value) return;
    const idx = queue.value.findIndex((t) => t.id === currentTrack.value!.id);
    if (idx < queue.value.length - 1) {
      play(queue.value[idx + 1]);
    }
  }

  function setQueue(tracks: Track[]) {
    queue.value = tracks;
  }

  return {
    currentTrack,
    queue,
    isPlaying,
    currentTime,
    volume,
    hasNext,
    play,
    pause,
    next,
    setQueue,
  };
});
