<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import {
  listUserActivities,
  type ActivityListResponse,
} from "@/api/activities";
import { listTracksWithMeta, type ListTracksResult } from "@/api/tracks";
import { listAlbums, type AlbumResponse } from "@/api/albums";
import { listLibraries, type LibraryResponse } from "@/api/libraries";
import { listPlaylists, type PlaylistResponse } from "@/api/playlists";
import { getApiErrorMessage } from "@/api/client";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import AlbumCard from "@/components/library/AlbumCard.vue";
import LibraryCard from "@/components/library/LibraryCard.vue";
import PlaylistCard from "@/components/library/PlaylistCard.vue";
import TrackList from "@/components/library/TrackList.vue";
import AppButton from "@/components/ui/AppButton.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import type { TrackResponse } from "@/player/types";

interface Props {
  tab: "posts" | "activity" | "tracks" | "albums" | "libraries" | "playlists";
}

const props = defineProps<Props>();
const { t } = useI18n();
const route = useRoute();

const username = computed(() => String(route.params.username));
const limit = 20;

const loading = ref(false);
const error = ref<string | null>(null);
const activities = ref<ActivityListResponse | null>(null);
const tracksResult = ref<ListTracksResult | null>(null);
const albums = ref<AlbumResponse[]>([]);
const libraries = ref<LibraryResponse[]>([]);
const playlists = ref<PlaylistResponse[]>([]);
const offset = ref(0);
const hasMoreEntities = ref(false);

const activitiesCursor = ref<string | null>(null);
const activitiesHasMore = ref(false);

const emptyEntity = computed(() => {
  switch (props.tab) {
    case "albums":
      return t("browse.entities.albums");
    case "libraries":
      return t("browse.entities.libraries");
    case "playlists":
      return t("browse.entities.playlists");
    case "tracks":
      return t("browse.entities.tracks");
    default:
      return t("browse.entities.item");
  }
});

const emptyMessage = computed(() =>
  t("browse.list.empty", { entity: emptyEntity.value }),
);

function reset() {
  activities.value = null;
  tracksResult.value = null;
  albums.value = [];
  libraries.value = [];
  playlists.value = [];
  offset.value = 0;
  hasMoreEntities.value = false;
  activitiesCursor.value = null;
  activitiesHasMore.value = false;
  error.value = null;
}

async function load(append = false) {
  loading.value = true;
  error.value = null;
  try {
    if (props.tab === "posts" || props.tab === "activity") {
      const mode = props.tab === "posts" ? "posts" : "all";
      const result = await listUserActivities(username.value, {
        mode,
        cursor: undefined,
        limit,
      });
      activities.value = result;
      activitiesCursor.value = result.next_cursor ?? null;
      activitiesHasMore.value =
        !!result.next_cursor || result.activities.length >= limit;
    } else if (props.tab === "tracks") {
      const result = await listTracksWithMeta({
        owner_username: username.value,
        limit,
        offset: offset.value,
      });
      if (append && tracksResult.value) {
        tracksResult.value = {
          ...result,
          tracks: [...tracksResult.value.tracks, ...result.tracks],
        };
      } else {
        tracksResult.value = result;
      }
      hasMoreEntities.value = result.tracks.length === limit;
    } else if (props.tab === "albums") {
      const result = await listAlbums({
        owner_username: username.value,
        limit,
        offset: offset.value,
      });
      if (append) {
        albums.value = [...albums.value, ...result];
      } else {
        albums.value = result;
      }
      hasMoreEntities.value = result.length === limit;
    } else if (props.tab === "libraries") {
      const result = await listLibraries({
        owner_username: username.value,
        limit,
        offset: offset.value,
      });
      if (append) {
        libraries.value = [...libraries.value, ...result];
      } else {
        libraries.value = result;
      }
      hasMoreEntities.value = result.length === limit;
    } else if (props.tab === "playlists") {
      const result = await listPlaylists({
        owner_username: username.value,
        limit,
        offset: offset.value,
      });
      if (append) {
        playlists.value = [...playlists.value, ...result];
      } else {
        playlists.value = result;
      }
      hasMoreEntities.value = result.length === limit;
    }
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("common.error");
    hasMoreEntities.value = false;
  } finally {
    loading.value = false;
  }
}

function loadMoreEntities() {
  if (loading.value || !hasMoreEntities.value) return;
  offset.value += limit;
  void load(true);
}

async function loadMoreActivities() {
  if (!activitiesCursor.value) return;
  try {
    const result = await listUserActivities(username.value, {
      mode: props.tab === "posts" ? "posts" : "all",
      cursor: activitiesCursor.value,
      limit,
    });
    activities.value = {
      ...result,
      activities: [
        ...(activities.value?.activities ?? []),
        ...result.activities,
      ],
    };
    activitiesCursor.value = result.next_cursor ?? null;
    activitiesHasMore.value =
      !!result.next_cursor || result.activities.length >= limit;
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("common.error");
  }
}

onMounted(() => {
  void load();
});
watch([() => props.tab, username], () => {
  reset();
  void load();
});
</script>

<template>
  <div class="user-profile-tab">
    <div
      v-if="
        loading &&
        !tracksResult &&
        !activities &&
        !albums.length &&
        !libraries.length &&
        !playlists.length
      "
      class="user-profile-tab__loading"
    >
      <SkeletonLoader v-for="n in 3" :key="n" variant="card" />
    </div>
    <div v-else-if="error" class="user-profile-tab__error" role="alert">
      {{ error }}
    </div>

    <template v-else-if="tab === 'posts' || tab === 'activity'">
      <p v-if="!activities?.activities.length" class="user-profile-tab__empty">
        {{ t("activities.empty") }}
      </p>
      <div v-else class="user-profile-tab__activities">
        <ActivityCard
          v-for="activity in activities.activities"
          :key="activity.id"
          :activity="activity"
        />
        <AppButton
          v-if="activitiesHasMore"
          variant="secondary"
          :loading="loading"
          @click="loadMoreActivities"
        >
          {{ t("activities.loadMore") }}
        </AppButton>
      </div>
    </template>

    <template v-else-if="tab === 'tracks'">
      <TrackList
        v-if="tracksResult"
        :tracks="tracksResult.tracks as TrackResponse[]"
        :loading="loading"
        :total="tracksResult.total"
        :offset="tracksResult.offset"
      />
      <p v-else class="user-profile-tab__empty">{{ emptyMessage }}</p>
    </template>

    <template v-else-if="tab === 'albums'">
      <p v-if="!albums.length" class="user-profile-tab__empty">
        {{ emptyMessage }}
      </p>
      <div v-else class="user-profile-tab__grid">
        <AlbumCard v-for="album in albums" :key="album.id" :album="album" />
      </div>
    </template>

    <template v-else-if="tab === 'libraries'">
      <p v-if="!libraries.length" class="user-profile-tab__empty">
        {{ emptyMessage }}
      </p>
      <div v-else class="user-profile-tab__grid">
        <LibraryCard
          v-for="library in libraries"
          :key="library.id"
          :library="library"
        />
      </div>
    </template>

    <template v-else-if="tab === 'playlists'">
      <p v-if="!playlists.length" class="user-profile-tab__empty">
        {{ emptyMessage }}
      </p>
      <div v-else class="user-profile-tab__grid">
        <PlaylistCard
          v-for="playlist in playlists"
          :key="playlist.id"
          :playlist="playlist"
        />
      </div>
    </template>

    <div
      v-if="
        ['albums', 'libraries', 'playlists'].includes(tab) &&
        !error &&
        hasMoreEntities
      "
      class="user-profile-tab__more"
    >
      <AppButton v-if="!loading" variant="secondary" @click="loadMoreEntities">
        {{ t("common.loadMore") }}
      </AppButton>
      <SkeletonLoader v-else variant="card" />
    </div>
  </div>
</template>

<style scoped>
.user-profile-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.user-profile-tab__loading,
.user-profile-tab__activities {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-3);
}

@media (min-width: 75rem) {
  .user-profile-tab__activities {
    align-items: center;
  }
}

.user-profile-tab__error {
  padding: var(--space-4);
  color: var(--color-danger);
  background-color: var(--color-surface);
  border-radius: var(--radius-md);
}

.user-profile-tab__empty {
  margin: 0;
  color: var(--color-text-muted);
}

.user-profile-tab__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}

.user-profile-tab__more {
  display: flex;
  justify-content: center;
  padding: var(--space-4) 0;
}
</style>
