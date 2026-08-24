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
  () => props.artistName ?? t("browse.entities.artist"),
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
      <span class="album-card__title">{{ props.album.title }}</span>
      <span v-if="props.album.release_year" class="album-card__year">
        {{ props.album.release_year }}
      </span>
    </RouterLink>
    <RouterLink
      v-if="artistLink"
      :to="artistLink"
      class="album-card__artist"
      @click.stop
    >
      {{ artistText }}
    </RouterLink>
    <span v-else-if="props.artistName" class="album-card__artist">
      {{ props.artistName }}
    </span>
  </div>
</template>

<style scoped>
.album-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
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
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.album-card__main:hover {
  background-color: var(--color-surface-raised);
}

.album-card__cover {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: var(--radius-md);
}

.album-card__title {
  font-weight: 600;
}

.album-card__year {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.album-card__artist {
  font-size: 0.875rem;
  color: var(--color-accent-contrast);
  text-decoration: none;
}

.album-card__artist:hover {
  text-decoration: underline;
}
</style>
