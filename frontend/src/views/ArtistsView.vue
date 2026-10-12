<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useChunkList } from "@/composables/useChunkList";
import { useRemoteEntities } from "@/composables/useRemoteEntities";
import {
  listArtistInitials,
  listArtistsWithMeta,
  deleteArtist,
  type ArtistResponse,
} from "@/api/artists";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
  type EntityListItem,
} from "@/utils/remoteEntities";
import { useAuthStore } from "@/stores/auth";
import ArtistCard from "@/components/library/ArtistCard.vue";
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
} = useChunkList<ArtistResponse>(
  (params) =>
    listArtistsWithMeta({
      ...params,
      collection: myCollection.value || undefined,
    }),
  {
    defaultSortBy: "name",
    defaultSortDir: "asc",
    defaultLimit: 50,
    syncQuery: true,
  },
);

// Federated artists render in the same grid, marked by RemoteEntityCard's
// globe badge and domain line. The remote fetch follows the same
// search/collection/sort state as the local list and pages in lockstep —
// Load More pulls the next page of each side.
const {
  items: remoteItems,
  hasMore: remoteHasMore,
  loadMore: loadMoreRemote,
} = useRemoteEntities("artist", {
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

const displayItems = computed<EntityListItem<ArtistResponse>[]>(() =>
  mergeEntityItems(items.value, windowedRemoteItems.value, {
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
    keyOf: (item, field) => {
      if (item.remote) return remoteEntitySortKey(item.entity, field);
      const artist = item.entity;
      if (field === "created_at") return artist.created_at ?? null;
      if (field === "updated_at") return artist.updated_at ?? null;
      return artist.name;
    },
    // A remote artist sharing a name with a local one clusters right
    // under it rather than scattering across the grid.
    groupKeyOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
  }),
);

const sortOptions = computed(() => [
  { value: "name", label: t("sort.fields.name") },
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
]);

// A–Z index only makes sense while the grid is alphabetized.
const getIndexKey = computed(() => {
  if (sortBy.value !== "name") return undefined;
  return (item: EntityListItem<ArtistResponse>) =>
    item.remote ? remoteEntityName(item.entity) : item.entity.name;
});

// Index letters for the whole filtered collection, mapped to the offset
// of their first item — `null` until fetched, which falls the grid back
// to letters found in the loaded fragment.
const initials = ref<Record<string, number> | null>(null);
const indexAvailable = computed(() =>
  initials.value ? new Set(Object.keys(initials.value)) : undefined,
);

async function fetchInitials() {
  if (sortBy.value !== "name") {
    initials.value = null;
    return;
  }
  try {
    const result = await listArtistInitials({
      q: query.value || undefined,
      collection: myCollection.value || undefined,
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
  <div class="artists-view">
    <BulkEditableGrid
      :title="t('nav.artists')"
      icon="users"
      :total="total"
      :items="displayItems"
      :loading="loading"
      :error="error"
      :has-more="mergedHasMore"
      :has-previous="hasPrevious"
      :loading-previous="loadingPrevious"
      :query="query"
      :entity-singular="t('browse.entities.artist')"
      :entity-plural="t('browse.entities.artists')"
      :delete-one="deleteArtist"
      :refresh="refresh"
      :get-name="
        (item) =>
          item.remote ? remoteEntityName(item.entity) : item.entity.name
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
        t('browse.delete.recursive', {
          contents: `${t('browse.entities.albums')} / ${t('browse.entities.tracks')}`,
        })
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
          class="artists-view__card"
          :object="item.entity"
          variant="avatar"
        />
        <ArtistCard
          v-else
          class="artists-view__card"
          :class="{ 'artists-view__card--selectable': bulkMode }"
          :artist="item.entity"
        />
      </template>
    </BulkEditableGrid>
  </div>
</template>

<style scoped>
.artists-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artists-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
}
</style>
