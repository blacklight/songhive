<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { usePlayerStore } from "@/stores/player";

export interface Props {
  mini?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  mini: false,
});

const { t } = useI18n();
const store = usePlayerStore();
const router = useRouter();

function goToAlbum() {
  const albumId = store.currentTrack?.album_id;
  if (albumId) {
    router.push(`/albums/${albumId}`);
  }
}
</script>

<template>
  <section
    class="now-playing"
    :class="{ 'now-playing--mini': props.mini }"
    role="region"
    :aria-label="t('player.nowPlaying')"
  >
    <button
      v-if="store.currentTrack?.artwork_url && !props.mini"
      class="now-playing__artwork"
      :class="{ 'now-playing__artwork--link': store.currentTrack?.album_id }"
      :aria-label="
        store.currentTrack?.album_id
          ? t('player.goToAlbum', {
              title: store.currentTrack?.album_title || '',
            }).trim()
          : t('player.albumArtwork')
      "
      @click="goToAlbum"
    >
      <img
        :src="store.currentTrack?.artwork_url"
        alt=""
        class="now-playing__img"
      />
    </button>
    <img
      v-else-if="store.currentTrack?.artwork_url && props.mini"
      :src="store.currentTrack?.artwork_url"
      alt=""
      class="now-playing__artwork now-playing__img"
    />
    <div v-else class="now-playing__artwork now-playing__artwork--placeholder">
      <svg
        viewBox="0 0 24 24"
        width="24"
        height="24"
        fill="currentColor"
        aria-hidden="true"
      >
        <path
          d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"
        />
      </svg>
    </div>

    <div class="now-playing__meta">
      <p class="now-playing__title" :title="store.currentTrack?.title">
        {{ store.currentTrack?.title }}
      </p>
      <p
        v-if="!props.mini"
        class="now-playing__artist"
        :title="store.currentTrack?.artist_name"
      >
        {{ store.currentTrack?.artist_name }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.now-playing {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.now-playing__artwork {
  flex-shrink: 0;
  width: 3.5rem;
  height: 3.5rem;
  border: none;
  padding: 0;
  background: transparent;
  border-radius: var(--radius-md);
  overflow: hidden;
  cursor: default;
}

.now-playing__artwork--link {
  cursor: pointer;
  transition: transform var(--transition-fast);
}

.now-playing__artwork--link:hover {
  transform: scale(1.03);
}

.now-playing__artwork--placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.now-playing__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.now-playing__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: var(--space-1);
}

.now-playing__title,
.now-playing__artist {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.now-playing__title {
  font-weight: 600;
  color: var(--color-text);
}

.now-playing__artist {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.now-playing--mini .now-playing__artwork {
  width: 2.75rem;
  height: 2.75rem;
}
</style>
