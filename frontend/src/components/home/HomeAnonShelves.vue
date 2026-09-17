<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { listAlbums } from "@/api/albums";
import { listGenres } from "@/api/genres";
import { listLibraries } from "@/api/libraries";
import { listTracks } from "@/api/tracks";
import { listPublicUsers } from "@/api/users";
import { useShelfData } from "@/composables/useShelfData";
import AlbumCard from "@/components/library/AlbumCard.vue";
import LibraryCard from "@/components/library/LibraryCard.vue";
import HomeChips, { type HomeChip } from "./HomeChips.vue";
import HomeShelf from "./HomeShelf.vue";
import HomeTrackCard from "./HomeTrackCard.vue";
import HomeUserCard from "./HomeUserCard.vue";

/**
 * The discovery zone of the anonymous home page: recently added public
 * content, public libraries, directory-visible people, and genre chips.
 * Every section is visibility-filtered server-side and removes itself when
 * empty.
 */
const { t } = useI18n();

const albums = useShelfData(() =>
  listAlbums({
    limit: 10,
    include: "artist",
    sort_by: "created_at",
    sort_dir: "desc",
  }),
);

const tracks = useShelfData(() =>
  listTracks({
    limit: 10,
    include: "artist",
    sort_by: "created_at",
    sort_dir: "desc",
  }),
);

const libraries = useShelfData(() =>
  listLibraries({ limit: 10, sort_by: "created_at", sort_dir: "desc" }),
);

const people = useShelfData(async () => {
  const { users } = await listPublicUsers({ limit: 8, sort_by: "created_at" });
  return users;
});

const genres = useShelfData<HomeChip>(async () => {
  const { items } = await listGenres({
    limit: 12,
    sort_by: "item_count",
    sort_dir: "desc",
  });
  return items.map((genre) => ({ name: genre.name, count: genre.item_count }));
});
</script>

<template>
  <div class="home-shelves">
    <HomeShelf
      :title="t('pages.home.shelves.recentAlbums')"
      see-all-to="/albums"
      :loading="albums.loading.value"
      :error="albums.error.value"
      :count="albums.items.value.length"
      @retry="albums.retry"
    >
      <AlbumCard
        v-for="album in albums.items.value"
        :key="album.id"
        :album="album"
      />
    </HomeShelf>

    <HomeShelf
      :title="t('pages.home.shelves.recentTracks')"
      see-all-to="/tracks"
      :loading="tracks.loading.value"
      :error="tracks.error.value"
      :count="tracks.items.value.length"
      @retry="tracks.retry"
    >
      <HomeTrackCard
        v-for="track in tracks.items.value"
        :key="track.id"
        :track-id="track.id"
        :title="track.title"
        :artist-name="track.artist?.name"
        :image-url="track.image_url"
      />
    </HomeShelf>

    <HomeShelf
      :title="t('pages.home.shelves.publicLibraries')"
      see-all-to="/libraries"
      :loading="libraries.loading.value"
      :error="libraries.error.value"
      :count="libraries.items.value.length"
      @retry="libraries.retry"
    >
      <LibraryCard
        v-for="library in libraries.items.value"
        :key="library.id"
        :library="library"
      />
    </HomeShelf>

    <HomeShelf
      :title="t('pages.home.shelves.people')"
      see-all-to="/users"
      :loading="people.loading.value"
      :error="people.error.value"
      :count="people.items.value.length"
      @retry="people.retry"
    >
      <HomeUserCard
        v-for="person in people.items.value"
        :key="person.username"
        :user="person"
      />
    </HomeShelf>

    <HomeChips
      :title="t('pages.home.shelves.exploreGenres')"
      :items="genres.items.value"
      base-path="/genres"
      see-all-to="/genres"
      :loading="genres.loading.value"
      :error="genres.error.value"
      @retry="genres.retry"
    />
  </div>
</template>

<style scoped>
.home-shelves {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
</style>
