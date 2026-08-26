<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import type { AlbumResponse } from "@/api/albums";
import AppAvatar from "@/components/ui/AppAvatar.vue";

export interface Props {
  album: AlbumResponse;
  artistName?: string;
}

const props = defineProps<Props>();
const emit = defineEmits<{ click: [album: AlbumResponse] }>();
const { t } = useI18n();

const artistLink = computed(() =>
  props.album.artist_id ? `/artists/${props.album.artist_id}` : undefined,
);

const artistText = computed(
  () =>
    props.artistName ?? props.album.artist?.name ?? t("browse.entities.artist"),
);
</script>

<template>
  <div class="album-card">
    <RouterLink
      :to="`/albums/${props.album.id}`"
      class="album-card__main"
      @click="emit('click', props.album)"
    >
      <img
        v-if="props.album.cover_url"
        :src="props.album.cover_url"
        :alt="props.album.title"
        class="album-card__cover"
      />
      <AppAvatar
        v-else
        :name="props.album.title"
        size="lg"
        class="album-card__cover"
      />
      <span :title="props.album.title" class="album-card__title">{{
        props.album.title
      }}</span>
      <RouterLink
        v-if="artistLink"
        :to="artistLink"
        :title="artistText"
        class="album-card__artist"
        @click.stop
      >
        {{ artistText }}
      </RouterLink>
      <span
        v-else-if="props.artistName"
        :title="props.artistName"
        class="album-card__artist"
      >
        {{ props.artistName }}
      </span>
      <span v-if="props.album.release_year" class="album-card__year">
        {{ props.album.release_year }}
      </span>
    </RouterLink>
  </div>
</template>

<style scoped>
.album-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

.album-card__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.album-card__cover,
.album-card__cover.app-avatar--lg {
  width: 100%;
  height: auto;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: var(--radius-md);
  font-size: clamp(2.5rem, 5vw, 4rem);
}

.album-card__title {
  font-weight: 600;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  flex: 0 0 auto;
  overflow: hidden;
  word-break: break-word;
}

.album-card__year {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.album-card__artist {
  display: inline-block;
  max-width: 100%;
  font-size: 0.875rem;
  color: var(--color-accent-contrast);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 0 0 auto;
}

.album-card__artist:hover {
  text-decoration: underline;
}
</style>
