<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { getTrack, type TrackResponse } from "@/api/tracks";
import { getAlbum, type AlbumResponse } from "@/api/albums";
import { getArtist, type ArtistResponse } from "@/api/artists";
import { getPlaylist, type PlaylistResponse } from "@/api/playlists";
import { getLibrary, type LibraryResponse } from "@/api/libraries";
import { getApiErrorMessage } from "@/api/client";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

export interface Props {
  type: string;
  id: string;
}

const props = defineProps<Props>();
const { t } = useI18n();

type Entity =
  | TrackResponse
  | AlbumResponse
  | ArtistResponse
  | PlaylistResponse
  | LibraryResponse;

const entity = ref<Entity | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const icon = computed(() => {
  const icons: Record<string, string> = {
    track: "music",
    album: "compact-disc",
    artist: "users",
    playlist: "list",
    library: "folder-open",
  };
  return icons[props.type] ?? "hashtag";
});

const title = computed(() => {
  if (!entity.value) return t("browse.entities.item");
  if ("title" in entity.value) {
    return entity.value.title;
  }
  if ("name" in entity.value) {
    return entity.value.name;
  }
  return t("browse.entities.item");
});

const coverUrl = computed(() => {
  if (!entity.value) return null;
  if ("cover_url" in entity.value) {
    return entity.value.cover_url ?? null;
  }
  if ("image_url" in entity.value) {
    return entity.value.image_url ?? null;
  }
  return null;
});

const subtitle = computed(() => {
  if (!entity.value) return "";
  if (props.type === "track" && "artist" in entity.value) {
    const track = entity.value as TrackResponse;
    return track.artist?.name ?? "";
  }
  if (props.type === "album" && "artist" in entity.value) {
    const album = entity.value as AlbumResponse;
    return album.artist?.name ?? "";
  }
  return "";
});

const link = computed(() => {
  const routes: Record<string, string> = {
    track: "/tracks",
    album: "/albums",
    artist: "/artists",
    playlist: "/playlists",
    library: "/libraries",
  };
  const base = routes[props.type] ?? "/";
  return `${base}/${props.id}`;
});

// TODO: Each card fetches its entity individually. Consider a batch-resolve
// endpoint (e.g. POST /hashtags/{name}/items:resolve) to avoid N+1 requests.
async function load() {
  loading.value = true;
  error.value = null;

  try {
    switch (props.type) {
      case "track":
        entity.value = await getTrack(props.id, { include: "artist,album" });
        break;
      case "album":
        entity.value = await getAlbum(props.id);
        break;
      case "artist":
        entity.value = await getArtist(props.id);
        break;
      case "playlist":
        entity.value = await getPlaylist(props.id);
        break;
      case "library":
        entity.value = await getLibrary(props.id);
        break;
      default:
        throw new Error(`Unknown entity type: ${props.type}`);
    }
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

onMounted(() => load());
</script>

<template>
  <div class="tagged-item-card">
    <RouterLink
      v-if="!loading && !error"
      :to="link"
      class="tagged-item-card__main"
    >
      <img
        v-if="coverUrl"
        :src="coverUrl"
        :alt="title"
        class="tagged-item-card__cover"
      />
      <AppAvatar
        v-else
        :name="title"
        size="lg"
        class="tagged-item-card__cover"
      />
      <span :title="title" class="tagged-item-card__title">{{ title }}</span>
      <span
        v-if="subtitle"
        :title="subtitle"
        class="tagged-item-card__subtitle"
      >
        {{ subtitle }}
      </span>
      <span class="tagged-item-card__type">
        <AppIcon :name="icon" />
        {{ t(`browse.entities.${type}`, type) }}
      </span>
    </RouterLink>

    <div v-else-if="loading" class="tagged-item-card__loading">
      <SkeletonLoader variant="card" />
    </div>

    <div v-else class="tagged-item-card__error" role="alert">
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.tagged-item-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.tagged-item-card__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.tagged-item-card__main:hover {
  background-color: var(--color-surface-hover);
}

.tagged-item-card__cover,
.tagged-item-card__cover.app-avatar--lg {
  width: 100%;
  height: auto;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: var(--radius-md);
  font-size: clamp(2.5rem, 5vw, 4rem);
}

.tagged-item-card__title {
  font-weight: 600;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.tagged-item-card__subtitle {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tagged-item-card__type {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.tagged-item-card__loading,
.tagged-item-card__error {
  min-height: 12rem;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
