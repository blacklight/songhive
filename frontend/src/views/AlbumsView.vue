<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useChunkList } from "@/composables/useChunkList";
import { useRemoteEntities } from "@/composables/useRemoteEntities";
import {
  listAlbumInitials,
  listAlbumsWithMeta,
  deleteAlbum,
  type AlbumResponse,
} from "@/api/albums";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
  type EntityListItem,
} from "@/utils/remoteEntities";
import { useAuthStore } from "@/stores/auth";
import AlbumCard from "@/components/library/AlbumCard.vue";
import RemoteEntityCard from "@/components/library/RemoteEntityCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const myCollection = ref(authStore.isAuthenticated);
const {
  items,
  loading,
  error,
  query,
  hasMore,
  hasPrevious,
  loadingPrevious,
  total,
  sortBy,
  sortDir,
  startOffset,
  load,
  loadMore,
  loadPrevious,
  loadAt,
  search,
  setSort,
  retry,
  refresh,
} = useChunkList<AlbumResponse>(
  (params) =>
    listAlbumsWithMeta({
      ...params,
      include: "artist",
      collection: myCollection.value || undefined,
    }),
  {
    defaultSortBy: "title",
    defaultSortDir: "asc",
    defaultLimit: 50,
    syncQuery: true,
  },
);

// Federated albums render in the same grid, marked by RemoteEntityCard's
// globe badge and domain line. The remote fetch follows the same
// search/collection/sort state as the local list and pages in lockstep —
// Load More pulls the next page of each side.
const {
  items: remoteItems,
  hasMore: remoteHasMore,
  loadMore: loadMoreRemote,
} = useRemoteEntities("album", {
  collection: myCollection,
  query,
  sortBy,
  sortDir,
});

const mergedHasMore = computed(() => hasMore.value || remoteHasMore.value);

// Remote entities page in from the top of the list, so they only merge
// into the window while it still covers offset 0 — in later fragments
// the index and ordering reflect the local collection only.
const windowedRemoteItems = computed(() =>
  startOffset.value === 0 ? remoteItems.value : [],
);

function loadMoreEntities() {
  void loadMore();
  void loadMoreRemote();
}

function sortKeyOf(item: EntityListItem<AlbumResponse>, field: string) {
  if (item.remote) return remoteEntitySortKey(item.entity, field);
  const album = item.entity;
  switch (field) {
    case "artist_name":
      return album.artist?.name ?? null;
    case "release_year":
      return album.release_year ?? null;
    case "created_at":
      return album.created_at ?? null;
    case "updated_at":
      return album.updated_at ?? null;
    default:
      return album.title;
  }
}

const displayItems = computed<EntityListItem<AlbumResponse>[]>(() =>
  mergeEntityItems(items.value, windowedRemoteItems.value, {
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.title,
    keyOf: sortKeyOf,
    // A remote album matching a local one's title and artist clusters
    // right under it rather than scattering across the grid.
    groupKeyOf: (item) =>
      item.remote
        ? `${remoteEntityName(item.entity)}|${item.entity.artist_name ?? ""}`
        : `${item.entity.title}|${item.entity.artist?.name ?? ""}`,
  }),
);

const sortOptions = computed(() => [
  { value: "title", label: t("sort.fields.title") },
  { value: "artist_name", label: t("sort.fields.artist_name") },
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
  { value: "release_year", label: t("sort.fields.release_year") },
]);

// Sort fields that alphabetize the grid — the A–Z index follows the
// active sort key (album title or artist name).
const INDEX_SORT_FIELDS = new Set(["title", "artist_name"]);

const getIndexKey = computed(() => {
  if (!INDEX_SORT_FIELDS.has(sortBy.value)) return undefined;
  const field = sortBy.value;
  return (item: EntityListItem<AlbumResponse>) => sortKeyOf(item, field);
});

// Index letters for the whole filtered collection, mapped to the offset
// of their first item — `null` until fetched, which falls the grid back
// to letters found in the loaded fragment.
const initials = ref<Record<string, number> | null>(null);
const indexAvailable = computed(() =>
  initials.value ? new Set(Object.keys(initials.value)) : undefined,
);

async function fetchInitials() {
  const field = sortBy.value;
  if (!INDEX_SORT_FIELDS.has(field)) {
    initials.value = null;
    return;
  }
  try {
    const result = await listAlbumInitials({
      q: query.value || undefined,
      collection: myCollection.value || undefined,
      sort_by: field as "title" | "artist_name",
      sort_dir: sortDir.value,
    });
    initials.value = result.letters;
  } catch {
    initials.value = null;
  }
}

watch([query, sortBy, sortDir, myCollection], () => void fetchInitials(), {
  immediate: true,
});

// Fetch the fragment containing a letter's first item when it lies
// outside the loaded window — no intermediate pages are requested.
async function resolveIndexJump(letter: string) {
  const anchor = initials.value?.[letter];
  if (anchor === undefined) return;
  if (
    anchor < startOffset.value ||
    anchor >= startOffset.value + items.value.length
  ) {
    await loadAt(anchor);
  }
}

function onSort(field: string, direction: "asc" | "desc") {
  void setSort(field, direction);
}

function onMyCollectionChange(value: boolean) {
  myCollection.value = value;
  void refresh();
}

onMounted(() => load());
</script>

<template>
  <div class="albums-view">
    <BulkEditableGrid
      :title="t('nav.albums')"
      icon="compact-disc"
      :total="total"
      :items="displayItems"
      :loading="loading"
      :error="error"
      :has-more="mergedHasMore"
      :has-previous="hasPrevious"
      :loading-previous="loadingPrevious"
      :query="query"
      :entity-singular="t('browse.entities.album')"
      :entity-plural="t('browse.entities.albums')"
      :delete-one="deleteAlbum"
      :refresh="refresh"
      :get-name="
        (item) =>
          item.remote ? remoteEntityName(item.entity) : item.entity.title
      "
      :can-manage="(item) => !item.remote"
      :search="search"
      :load-more="loadMoreEntities"
      :load-previous="loadPrevious"
      :retry="retry"
      :sort-by="sortBy"
      :sort-dir="sortDir"
      :sort-options="sortOptions"
      :get-index-key="getIndexKey"
      :index-available="indexAvailable"
      :resolve-index-jump="resolveIndexJump"
      :recursive="true"
      :recursive-label="
        t('browse.delete.recursive', { contents: t('browse.entities.tracks') })
      "
      @sort="onSort"
    >
      <template #filters>
        <CollectionToggle
          :model-value="myCollection"
          @update:model-value="onMyCollectionChange"
        />
      </template>

      <template #card="{ item, bulkMode }">
        <RemoteEntityCard
          v-if="item.remote"
          class="albums-view__card"
          :object="item.entity"
        />
        <AlbumCard
          v-else
          class="albums-view__card"
          :class="{ 'albums-view__card--selectable': bulkMode }"
          :album="item.entity"
        />
      </template>
    </BulkEditableGrid>
  </div>
</template>

<style scoped>
.albums-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.albums-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
}
</style>
