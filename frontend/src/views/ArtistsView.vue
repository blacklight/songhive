<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listArtists, deleteArtist, type ArtistResponse } from "@/api/artists";
import { useAuthStore } from "@/stores/auth";
import { useBulkDelete } from "@/composables/useBulkDelete";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ArtistCard from "@/components/library/ArtistCard.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const {
  items,
  loading,
  error,
  query,
  hasMore,
  load,
  loadMore,
  search,
  retry,
  refresh,
} = useEntityList<ArtistResponse>(listArtists);

const bulk = useBulkDelete<ArtistResponse>({
  deleteOne: deleteArtist,
  refresh,
  entitySingular: t("browse.entities.artist"),
  entityPlural: t("browse.entities.artists"),
  getName: (artist) => artist.name,
  recursive: true,
  recursiveLabel: t("browse.delete.recursive", {
    contents: `${t("browse.entities.albums")} / ${t("browse.entities.tracks")}`,
  }),
});

const {
  bulkMode,
  selectedIds,
  isDeleting,
  deleteModalOpen,
  deleteModalTitle,
  deleteModalMessage,
  deleteModalAllowRecursive,
  deleteModalLoading,
  recursiveLabel,
} = bulk;

const manageableArtists = computed(() =>
  items.value.filter((artist) => bulk.canManage(artist)),
);

const allSelected = computed(() => bulk.allSelected(manageableArtists.value));
const someSelected = computed(() => bulk.someSelected(manageableArtists.value));

onMounted(() => load());
</script>

<template>
  <div class="artists-view">
    <div class="artists-view__header">
      <AppPageTitle class="artists-view__title" icon="users">{{
        t("nav.artists")
      }}</AppPageTitle>

      <div class="artists-view__actions">
        <template v-if="authStore.isAuthenticated && !bulkMode">
          <AppButton
            size="sm"
            icon="pen-to-square"
            variant="secondary"
            @click="bulk.enterBulkMode"
          >
            {{ t("browse.bulkEdit.start") }}
          </AppButton>
        </template>

        <template v-else-if="authStore.isAuthenticated">
          <AppCheckbox
            :model-value="allSelected"
            :indeterminate="someSelected"
            :label="t('browse.bulkEdit.selectAll')"
            @update:model-value="bulk.toggleAll(items)"
          />
          <AppButton
            variant="danger"
            size="sm"
            icon="trash"
            :disabled="selectedIds.size === 0 || isDeleting"
            :loading="isDeleting"
            @click="bulk.openDeleteBulk(items)"
          >
            {{ t("browse.bulkEdit.deleteSelected") }}
          </AppButton>
          <AppButton
            size="sm"
            icon="xmark"
            variant="secondary"
            :disabled="isDeleting"
            @click="bulk.exitBulkMode"
          >
            {{ t("browse.bulkEdit.done") }}
          </AppButton>
        </template>
      </div>
    </div>

    <!--
      :debounce="0" avoids stacking with useEntityList's 300 ms debounce;
      the composable owns the real debounce.
    -->
    <SearchBar
      :model-value="query"
      :debounce="0"
      class="artists-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.artists'),
        })
      "
      @update:model-value="search"
    />

    <div
      v-if="loading && items.length === 0"
      class="artists-view__grid artists-view__grid--skeleton"
    >
      <SkeletonLoader v-for="i in 8" :key="i" variant="card" />
    </div>

    <div v-else-if="error" class="artists-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <div v-else-if="items.length === 0" class="artists-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.artists") }) }}
    </div>

    <div
      v-else
      class="artists-view__grid"
      :class="{ 'artists-view__grid--bulk': bulkMode }"
    >
      <div
        v-for="artist in items"
        :key="artist.id"
        class="artists-view__card-wrapper"
      >
        <ArtistCard
          class="artists-view__card"
          :class="{ 'artists-view__card--selectable': bulkMode }"
          :artist="artist"
        />

        <AppCheckbox
          v-if="bulkMode"
          class="artists-view__card-checkbox"
          :model-value="selectedIds.has(artist.id)"
          :disabled="!bulk.canManage(artist)"
          @update:model-value="bulk.toggleSelect(artist.id)"
        />

        <AppButton
          v-else-if="bulk.canManage(artist)"
          class="artists-view__card-delete"
          variant="danger"
          size="sm"
          icon="trash"
          :title="t('common.delete')"
          @click="bulk.openDeleteSingle(artist)"
        />
      </div>
    </div>

    <div class="artists-view__footer">
      <AppButton
        v-if="hasMore"
        icon="chevron-down"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
      <AppSpinner v-else-if="loading" />
    </div>

    <DeleteModal
      :open="deleteModalOpen"
      :title="deleteModalTitle"
      :message="deleteModalMessage"
      :allow-recursive="deleteModalAllowRecursive"
      :recursive-label="recursiveLabel"
      :loading="deleteModalLoading"
      @close="bulk.closeDeleteModal"
      @confirm="bulk.confirmDelete"
    />
  </div>
</template>

<style scoped>
.artists-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artists-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.artists-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.artists-view__actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.artists-view__search {
  max-width: 32rem;
}

.artists-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}

.artists-view__grid--bulk .artists-view__card {
  pointer-events: none;
  opacity: 0.8;
}

.artists-view__card-wrapper {
  position: relative;
}

.artists-view__card {
  display: block;
}

.artists-view__card-checkbox {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  padding: var(--space-1);
}

.artists-view__card-delete {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  z-index: 1;
}

.artists-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.artists-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.artists-view__footer {
  display: flex;
  justify-content: center;
}
</style>
