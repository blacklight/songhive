<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useChunkList } from "@/composables/useChunkList";
import { useRemoteEntities } from "@/composables/useRemoteEntities";
import {
  listPlaylistInitials,
  listPlaylistsWithMeta,
  createPlaylist,
  deletePlaylist,
  type PlaylistResponse,
  type PlaylistCreate,
} from "@/api/playlists";
import {
  mergeEntityItems,
  remoteEntityName,
  remoteEntitySortKey,
  type EntityListItem,
} from "@/utils/remoteEntities";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import PlaylistCard from "@/components/library/PlaylistCard.vue";
import RemoteEntityCard from "@/components/library/RemoteEntityCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";
import CollectionToggle from "@/components/ui/CollectionToggle.vue";
import type { Visibility } from "@/api/playlists";

const { t } = useI18n();
const authStore = useAuthStore();
const toastStore = useToastStore();
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
} = useChunkList<PlaylistResponse>(
  (params) =>
    listPlaylistsWithMeta({
      ...params,
      include: "owner",
      collection: myCollection.value || undefined,
    }),
  {
    defaultSortBy: "name",
    defaultSortDir: "asc",
    defaultLimit: 50,
    syncQuery: true,
  },
);

// Federated playlists render in the same grid, marked by RemoteEntityCard's
// globe badge and domain line. The remote fetch follows the same
// search/collection/sort state as the local list and pages in lockstep —
// Load More pulls the next page of each side.
const {
  items: remoteItems,
  hasMore: remoteHasMore,
  loadMore: loadMoreRemote,
} = useRemoteEntities("playlist", {
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

const displayItems = computed<EntityListItem<PlaylistResponse>[]>(() =>
  mergeEntityItems(items.value, windowedRemoteItems.value, {
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    nameOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
    keyOf: (item, field) => {
      if (item.remote) return remoteEntitySortKey(item.entity, field);
      const playlist = item.entity;
      if (field === "created_at") return playlist.created_at ?? null;
      if (field === "updated_at") return playlist.updated_at ?? null;
      return playlist.name;
    },
    // Same-named remote playlists cluster under their local twin.
    groupKeyOf: (item) =>
      item.remote ? remoteEntityName(item.entity) : item.entity.name,
  }),
);

const isCreateOpen = ref(false);
const name = ref("");
const description = ref("");
const visibility = ref<Visibility>("private");
const createError = ref<string | null>(null);
const isCreating = ref(false);

const canCreate = computed(() => authStore.isAuthenticated);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

const sortOptions = computed(() => [
  { value: "name", label: t("sort.fields.name") },
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "updated_at", label: t("sort.fields.updated_at") },
]);

// A–Z index only makes sense while the grid is alphabetized.
const getIndexKey = computed(() => {
  if (sortBy.value !== "name") return undefined;
  return (item: EntityListItem<PlaylistResponse>) =>
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
    const result = await listPlaylistInitials({
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

function openCreate() {
  name.value = "";
  description.value = "";
  visibility.value = "private";
  createError.value = null;
  isCreateOpen.value = true;
}

function closeCreate() {
  isCreateOpen.value = false;
}

async function onCreate() {
  createError.value = null;
  if (!name.value.trim()) return;

  isCreating.value = true;
  const body: PlaylistCreate = {
    name: name.value.trim(),
    description: description.value.trim() || null,
  };

  try {
    await createPlaylist(body, { visibility: visibility.value });
    toastStore.push({ type: "success", message: t("browse.createPlaylist") });
    closeCreate();
    await refresh();
  } catch (err) {
    createError.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    isCreating.value = false;
  }
}
</script>

<template>
  <div class="playlists-view">
    <BulkEditableGrid
      :title="t('nav.playlists')"
      icon="list"
      :total="total"
      :items="displayItems"
      :loading="loading"
      :error="error"
      :has-more="mergedHasMore"
      :has-previous="hasPrevious"
      :loading-previous="loadingPrevious"
      :query="query"
      :entity-singular="t('browse.entities.playlist')"
      :entity-plural="t('browse.entities.playlists')"
      :delete-one="deletePlaylist"
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
        t('browse.delete.recursive', { contents: t('browse.entities.tracks') })
      "
      grid-min-width="16rem"
      @sort="onSort"
    >
      <template #header-actions="{ bulkMode }">
        <AppButton
          v-if="canCreate && !bulkMode"
          size="sm"
          icon="plus"
          @click="openCreate"
        >
          {{ t("browse.list.createPlaylist") }}
        </AppButton>
      </template>

      <template #filters>
        <CollectionToggle
          :model-value="myCollection"
          @update:model-value="onMyCollectionChange"
        />
      </template>

      <template #card="{ item, bulkMode }">
        <RemoteEntityCard
          v-if="item.remote"
          class="playlists-view__card"
          :object="item.entity"
        />
        <PlaylistCard
          v-else
          class="playlists-view__card"
          :class="{ 'playlists-view__card--selectable': bulkMode }"
          :playlist="item.entity"
        />
      </template>
    </BulkEditableGrid>

    <AppModal
      :open="isCreateOpen"
      :title="t('browse.list.newPlaylist')"
      @close="closeCreate"
    >
      <form
        id="create-playlist-form"
        class="playlists-view__create-form"
        @submit.prevent="onCreate"
      >
        <AppInput
          v-model="name"
          :label="t('browse.edit.name')"
          :required="true"
        />
        <AppInput
          v-model="description"
          as="textarea"
          :label="t('browse.edit.description')"
        />
        <AppSelect
          v-model="visibility"
          :label="t('browse.detail.visibility')"
          :options="visibilityOptions"
        />
        <p v-if="createError" class="playlists-view__create-error" role="alert">
          {{ createError }}
        </p>
      </form>

      <template #actions>
        <AppButton variant="secondary" icon="xmark" @click="closeCreate">
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          form="create-playlist-form"
          type="submit"
          :loading="isCreating"
          :disabled="isCreating || !name.trim()"
          icon="floppy-disk"
        >
          {{ t("common.save") }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.playlists-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.playlists-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
}

.playlists-view__create-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.playlists-view__create-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
