<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { getTrack } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { toQueueTrack } from "@/player/enrich";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";

/**
 * Compact track tile for home-page shelves. Works with a bare
 * ``trackId``/``title``/``artistName`` triple as well as full track
 * records; ``imageUrl`` is optional and falls back to an initial avatar.
 */
export interface Props {
  trackId: string;
  title: string;
  artistName?: string | null;
  imageUrl?: string | null;
}

const props = defineProps<Props>();
const { t } = useI18n();
const player = usePlayerStore();
const toast = useToastStore();

async function onPlay() {
  try {
    const track = await getTrack(props.trackId);
    player.playTrack(
      toQueueTrack(track, { artist_name: props.artistName ?? "" }),
    );
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.home.playError", {
        message:
          getApiErrorMessage(err) ||
          (err instanceof Error ? err.message : t("errors.unknown")),
      }),
    });
  }
}
</script>

<template>
  <div class="home-track-card">
    <RouterLink :to="`/tracks/${props.trackId}`" class="home-track-card__main">
      <img
        v-if="props.imageUrl"
        :src="props.imageUrl"
        :alt="props.title"
        class="home-track-card__cover"
      />
      <AppAvatar
        v-else
        :name="props.title"
        size="lg"
        class="home-track-card__cover"
      />
      <span :title="props.title" class="home-track-card__title">{{
        props.title
      }}</span>
      <span
        v-if="props.artistName"
        :title="props.artistName"
        class="home-track-card__artist"
        >{{ props.artistName }}</span
      >
    </RouterLink>
    <AppButton
      size="sm"
      variant="secondary"
      icon="play"
      class="home-track-card__play"
      :aria-label="t('common.play')"
      :title="t('common.play')"
      @click="onPlay"
    />
  </div>
</template>

<style scoped>
.home-track-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
}

.home-track-card__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  color: var(--color-text);
  text-decoration: none;
}

.home-track-card__cover,
.home-track-card__cover.app-avatar--lg {
  width: 100%;
  height: auto;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: var(--radius-md);
  font-size: clamp(2rem, 4vw, 3rem);
}

.home-track-card__title {
  font-weight: 600;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.home-track-card__artist {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-track-card__play {
  position: absolute;
  right: var(--space-3);
  bottom: var(--space-3);
}
</style>
