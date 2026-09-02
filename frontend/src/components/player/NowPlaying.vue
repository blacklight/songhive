<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { usePlayerStore } from "@/stores/player";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  mini?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  mini: false,
});

const { t } = useI18n();
const store = usePlayerStore();
</script>

<template>
  <section
    class="now-playing"
    :class="{ 'now-playing--mini': props.mini }"
    role="region"
    :aria-label="t('player.nowPlaying')"
  >
    <RouterLink
      v-if="store.currentTrack?.album_id"
      :to="`/albums/${store.currentTrack.album_id}`"
      class="now-playing__artwork now-playing__artwork--link"
      :aria-label="
        t('player.goToAlbum', {
          title: store.currentTrack?.album_title || '',
        }).trim()
      "
      :title="store.currentTrack?.album_title"
    >
      <img
        v-if="store.currentTrack?.artwork_url"
        :src="store.currentTrack.artwork_url"
        alt=""
        class="now-playing__img"
      />
      <div v-else class="now-playing__artwork--placeholder">
        <AppIcon name="music" />
      </div>
    </RouterLink>
    <img
      v-else-if="store.currentTrack?.artwork_url"
      :src="store.currentTrack.artwork_url"
      alt=""
      class="now-playing__artwork now-playing__img"
    />
    <div v-else class="now-playing__artwork now-playing__artwork--placeholder">
      <AppIcon name="music" />
    </div>

    <div class="now-playing__meta">
      <RouterLink
        v-if="store.currentTrack?.id"
        :to="`/tracks/${store.currentTrack.id}`"
        class="now-playing__title now-playing__title--link"
        :title="store.currentTrack?.title"
        :aria-label="
          t('player.goToTrack', {
            title: store.currentTrack?.title || '',
          }).trim()
        "
      >
        {{ store.currentTrack?.title }}
      </RouterLink>
      <p v-else class="now-playing__title" :title="store.currentTrack?.title">
        {{ store.currentTrack?.title }}
      </p>
      <RouterLink
        v-if="store.currentTrack?.artist_id"
        :to="`/artists/${store.currentTrack.artist_id}`"
        class="now-playing__artist now-playing__artist--link"
        :title="store.currentTrack?.artist_name"
        :aria-label="
          t('player.goToArtist', {
            name: store.currentTrack?.artist_name || '',
          }).trim()
        "
      >
        {{ store.currentTrack?.artist_name }}
      </RouterLink>
      <p
        v-else
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
  display: inline-flex;
  cursor: pointer;
  transition: transform var(--transition-fast);
  text-decoration: none;
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

.now-playing__artwork--link .now-playing__artwork--placeholder {
  width: 100%;
  height: 100%;
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

.now-playing__title--link,
.now-playing__artist--link {
  display: block;
  text-decoration: none;
}

.now-playing__title--link:hover,
.now-playing__title--link:focus-visible,
.now-playing__artist--link:hover,
.now-playing__artist--link:focus-visible {
  color: var(--color-text);
  text-decoration: underline;
}

.now-playing__title--link:focus-visible,
.now-playing__artist--link:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.now-playing--mini .now-playing__artwork {
  width: 2.75rem;
  height: 2.75rem;
}
</style>
