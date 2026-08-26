<script setup lang="ts">
import { RouterLink } from "vue-router";
import type { ArtistResponse } from "@/api/artists";
import AppAvatar from "@/components/ui/AppAvatar.vue";

export interface Props {
  artist: ArtistResponse;
}

const props = defineProps<Props>();
const emit = defineEmits<{ click: [artist: ArtistResponse] }>();
</script>

<template>
  <RouterLink
    :to="`/artists/${props.artist.id}`"
    class="artist-card"
    @click="emit('click', props.artist)"
  >
    <AppAvatar
      :src="props.artist.image_url ?? undefined"
      :name="props.artist.name"
      size="lg"
      class="artist-card__avatar"
    />
    <span :title="props.artist.name" class="artist-card__name">{{
      props.artist.name
    }}</span>
    <span
      v-if="props.artist.bio"
      :title="props.artist.bio"
      class="artist-card__bio"
    >
      {{ props.artist.bio }}
    </span>
  </RouterLink>
</template>

<style scoped>
.artist-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  width: 100%;
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.artist-card:hover {
  background-color: var(--color-surface-hover);
}

.artist-card__avatar {
  width: 6rem;
  height: 6rem;
  flex-shrink: 0;
  margin-top: var(--space-4);
}

.artist-card__name {
  font-weight: 600;
  width: 100%;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: var(--space-4);
}

.artist-card__bio {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  width: 100%;
  text-align: center;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
</style>
