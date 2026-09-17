<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { listAlbums } from "@/api/albums";
import { listHistory } from "@/api/history";
import { listTracks, type TrackResponse } from "@/api/tracks";
import { useAuthStore } from "@/stores/auth";
import { useShelfData } from "@/composables/useShelfData";
import AlbumCard from "@/components/library/AlbumCard.vue";
import HomeShelf from "./HomeShelf.vue";
import HomeTrackCard from "./HomeTrackCard.vue";

/**
 * The "your music" zone of the authenticated home page: at most four
 * shelves (jump back in, favorites, uploads, new on this instance), each
 * fetching independently and removing itself when empty.
 */
const { t } = useI18n();
const authStore = useAuthStore();

const username = computed(() => authStore.user?.username ?? "");

interface JumpBackInItem {
  trackId: string;
  title: string;
  artistName?: string | null;
}

const jumpBackIn = useShelfData<JumpBackInItem>(async () => {
  const { items } = await listHistory({ pageSize: 12 });
  const seen = new Set<string>();
  return items
    .filter((entry) => !seen.has(entry.track_id) && seen.add(entry.track_id))
    .map((entry) => ({
      trackId: entry.track_id,
      title: entry.title ?? t("pages.history.untitled"),
      artistName: entry.artist,
    }));
});

const favorites = useShelfData<TrackResponse>(() =>
  listTracks({
    favorited: true,
    limit: 12,
    include: "artist",
    sort_by: "created_at",
    sort_dir: "desc",
  }),
);

const uploads = useShelfData<TrackResponse>(() =>
  listTracks({
    owner_username: username.value,
    limit: 12,
    include: "artist",
    sort_by: "created_at",
    sort_dir: "desc",
  }),
);

const newAlbums = useShelfData(() =>
  listAlbums({
    limit: 10,
    include: "artist",
    sort_by: "created_at",
    sort_dir: "desc",
  }),
);
</script>

<template>
  <div class="home-shelves">
    <HomeShelf
      :title="t('pages.home.shelves.jumpBackIn')"
      see-all-to="/history"
      :loading="jumpBackIn.loading.value"
      :error="jumpBackIn.error.value"
      :count="jumpBackIn.items.value.length"
      @retry="jumpBackIn.retry"
    >
      <HomeTrackCard
        v-for="item in jumpBackIn.items.value"
        :key="item.trackId"
        :track-id="item.trackId"
        :title="item.title"
        :artist-name="item.artistName"
      />
    </HomeShelf>

    <HomeShelf
      :title="t('pages.home.shelves.favorites')"
      see-all-to="/favorites"
      :loading="favorites.loading.value"
      :error="favorites.error.value"
      :count="favorites.items.value.length"
      @retry="favorites.retry"
    >
      <HomeTrackCard
        v-for="track in favorites.items.value"
        :key="track.id"
        :track-id="track.id"
        :title="track.title"
        :artist-name="track.artist?.name"
        :image-url="track.image_url"
      />
    </HomeShelf>

    <HomeShelf
      :title="t('pages.home.shelves.uploads')"
      :see-all-to="`/@${username}/tracks`"
      :loading="uploads.loading.value"
      :error="uploads.error.value"
      :count="uploads.items.value.length"
      @retry="uploads.retry"
    >
      <HomeTrackCard
        v-for="track in uploads.items.value"
        :key="track.id"
        :track-id="track.id"
        :title="track.title"
        :artist-name="track.artist?.name"
        :image-url="track.image_url"
      />
    </HomeShelf>

    <HomeShelf
      :title="t('pages.home.shelves.newOnInstance')"
      see-all-to="/albums"
      :loading="newAlbums.loading.value"
      :error="newAlbums.error.value"
      :count="newAlbums.items.value.length"
      @retry="newAlbums.retry"
    >
      <AlbumCard
        v-for="album in newAlbums.items.value"
        :key="album.id"
        :album="album"
      />
    </HomeShelf>
  </div>
</template>

<style scoped>
.home-shelves {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
</style>
