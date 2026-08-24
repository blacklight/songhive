import { onMounted, onUnmounted, watch } from "vue";
import { storeToRefs } from "pinia";
import { usePlayerStore } from "@/stores/player";

export function useMediaSession() {
  const store = usePlayerStore();
  const { currentTrack, isPlaying } = storeToRefs(store);

  if (!("mediaSession" in navigator)) {
    return;
  }

  const mediaSession = navigator.mediaSession;

  watch(
    currentTrack,
    (track) => {
      if (!track) {
        mediaSession.metadata = null;
        return;
      }

      try {
        const artwork = track.artwork_url ? [{ src: track.artwork_url }] : [];
        mediaSession.metadata = new MediaMetadata({
          title: track.title,
          artist: track.artist_name,
          album: track.album_title ?? "",
          artwork,
        });
      } catch {
        // MediaMetadata may not be available in all environments.
      }
    },
    { immediate: true },
  );

  watch(
    isPlaying,
    (isPlaying) => {
      try {
        mediaSession.playbackState = isPlaying ? "playing" : "paused";
      } catch {
        // Ignore environments that do not support playbackState.
      }
    },
    { immediate: true },
  );

  onMounted(() => {
    function setHandler(action: MediaSessionAction, handler: () => void) {
      try {
        mediaSession.setActionHandler(action, handler);
      } catch {
        // Ignore unsupported action handlers.
      }
    }

    setHandler("play", () => store.play());
    setHandler("pause", () => store.pause());
    setHandler("nexttrack", () => store.next());
    setHandler("previoustrack", () => store.prev());
    setHandler("seekto", (details?: MediaSessionActionDetails) => {
      const seekTime = details?.seekTime;
      if (typeof seekTime === "number" && !Number.isNaN(seekTime)) {
        store.seek(seekTime);
      }
    });
  });

  onUnmounted(() => {
    try {
      mediaSession.setActionHandler("play", null);
      mediaSession.setActionHandler("pause", null);
      mediaSession.setActionHandler("nexttrack", null);
      mediaSession.setActionHandler("previoustrack", null);
      mediaSession.setActionHandler("seekto", null);
      mediaSession.metadata = null;
      mediaSession.playbackState = "none";
    } catch {
      // Ignore errors during cleanup.
    }
  });
}
