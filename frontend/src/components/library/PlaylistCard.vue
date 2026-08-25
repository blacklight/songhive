<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import type { PlaylistResponse } from "@/api/playlists";
import AppAvatar from "@/components/ui/AppAvatar.vue";

export interface Props {
  playlist: PlaylistResponse;
}

const props = defineProps<Props>();
const emit = defineEmits<{ click: [playlist: PlaylistResponse] }>();
const { t } = useI18n();
const authStore = useAuthStore();

const ownerName = computed(() => {
  const ownerId = props.playlist.owner_id;
  if (!ownerId) return "";
  if (authStore.user?.id === ownerId) {
    return authStore.user.display_name ?? authStore.user.username;
  }
  // The backend only returns owner_id for other users; resolving their
  // display names requires a denormalized field or a user lookup.
  return ownerId;
});

const visibilityText = computed(() => {
  const labels: Record<string, string> = {
    private: t("browse.visibility.private"),
    local: t("browse.visibility.local"),
    public: t("browse.visibility.public"),
  };
  return labels[props.playlist.visibility] ?? props.playlist.visibility;
});
</script>

<template>
  <RouterLink
    :to="`/playlists/${props.playlist.id}`"
    class="playlist-card"
    @click="emit('click', props.playlist)"
  >
    <AppAvatar
      :name="props.playlist.name"
      size="lg"
      class="playlist-card__avatar"
    />
    <span :title="props.playlist.name" class="playlist-card__name">{{
      props.playlist.name
    }}</span>
    <span
      v-if="props.playlist.description"
      :title="props.playlist.description"
      class="playlist-card__description"
    >
      {{ props.playlist.description }}
    </span>
    <div class="playlist-card__meta">
      <span :title="visibilityText" class="playlist-card__visibility">{{
        visibilityText
      }}</span>
      <span v-if="ownerName" :title="ownerName" class="playlist-card__owner">
        {{ t("browse.detail.owner") }} {{ ownerName }}
      </span>
    </div>
  </RouterLink>
</template>

<style scoped>
.playlist-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.playlist-card:hover {
  background-color: var(--color-bg-hover);
}

.playlist-card__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.playlist-card__description {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.playlist-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.playlist-card__visibility,
.playlist-card__owner {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
