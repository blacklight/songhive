<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { listAlbums, deleteAlbum, type AlbumResponse } from "@/api/albums";
import { useAuthStore } from "@/stores/auth";
import { useBulkDelete } from "@/composables/useBulkDelete";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AlbumCard from "@/components/library/AlbumCard.vue";
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
} = useEntityList<AlbumResponse>(listAlbums);

const bulk = useBulkDelete<AlbumResponse>({
  deleteOne: deleteAlbum,
  refresh,
  entitySingular: t("browse.entities.album"),
  entityPlural: t("browse.entities.albums"),
  getName: (album) => album.title,
  recursive: true,
  recursiveLabel: t("browse.delete.recursive", {
    contents: t("browse.entities.tracks"),
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

const manageableAlbums = computed(() =>
  items.value.filter((album) => bulk.canManage(album)),
);

const allSelected = computed(() => bulk.allSelected(manageableAlbums.value));
const someSelected = computed(() => bulk.someSelected(manageableAlbums.value));

onMounted(() => load());
</script>

<template>
  <div class="albums-view">
    <div class="albums-view__header">
      <AppPageTitle class="albums-view__title" icon="compact-disc">
        {{ t("nav.albums") }}
      </AppPageTitle>

      <div class="albums-view__actions">
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
      class="albums-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.albums'),
        })
      "
      @update:model-value="search"
    />

    <div
      v-if="loading && items.length === 0"
      class="albums-view__grid albums-view__grid--skeleton"
    >
      <SkeletonLoader v-for="i in 8" :key="i" variant="card" />
    </div>

    <div v-else-if="error" class="albums-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div v-else-if="items.length === 0" class="albums-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.albums") }) }}
    </div>

    <div
      v-else
      class="albums-view__grid"
      :class="{ 'albums-view__grid--bulk': bulkMode }"
    >
      <div
        v-for="album in items"
        :key="album.id"
        class="albums-view__card-wrapper"
      >
        <AlbumCard
          class="albums-view__card"
          :class="{ 'albums-view__card--selectable': bulkMode }"
          :album="album"
        />

        <AppCheckbox
          v-if="bulkMode"
          class="albums-view__card-checkbox"
          :model-value="selectedIds.has(album.id)"
          :disabled="!bulk.canManage(album)"
          @update:model-value="bulk.toggleSelect(album.id)"
        />

        <AppButton
          v-else-if="bulk.canManage(album)"
          class="albums-view__card-delete"
          variant="danger"
          size="sm"
          icon="trash"
          :title="t('common.delete')"
          @click="bulk.openDeleteSingle(album)"
        />
      </div>
    </div>

    <div class="albums-view__footer">
      <AppButton
        v-if="hasMore"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        icon="chevron-down"
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
.albums-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.albums-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.albums-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.albums-view__actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.albums-view__search {
  max-width: 32rem;
}

.albums-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}

.albums-view__grid--bulk .albums-view__card {
  pointer-events: none;
  opacity: 0.8;
}

.albums-view__card-wrapper {
  position: relative;
}

.albums-view__card {
  display: block;
}

.albums-view__card-checkbox {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  padding: var(--space-1);
}

.albums-view__card-delete {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  z-index: 1;
}

.albums-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.albums-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.albums-view__footer {
  display: flex;
  justify-content: center;
}
</style>
