<script setup lang="ts" generic="T extends ManageableItem">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import {
  useBulkDelete,
  type ManageableItem,
} from "@/composables/useBulkDelete";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";

export interface Props<T extends ManageableItem> {
  title: string;
  icon?: string;
  items: T[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  query: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  entitySingular: string;
  entityPlural: string;
  deleteOne: (id: string, recursive: boolean) => Promise<void>;
  refresh: () => Promise<void> | void;
  getName: (item: T) => string;
  getOwnerId?: (item: T) => string | null | undefined;
  recursive?: boolean;
  recursiveLabel?: string;
  layout?: "grid" | "list";
  gridMinWidth?: string;
  itemClass?: string;
  search: (q: string) => void | Promise<void>;
  loadMore: () => void | Promise<void>;
  retry: () => void | Promise<void>;
}

const props = withDefaults(defineProps<Props<T>>(), {
  icon: undefined,
  searchPlaceholder: undefined,
  emptyMessage: undefined,
  getOwnerId: undefined,
  recursive: false,
  recursiveLabel: undefined,
  layout: "grid",
  gridMinWidth: "12rem",
  itemClass: undefined,
});

const emit = defineEmits<{
  "update:query": [value: string];
}>();

const { t } = useI18n();
const authStore = useAuthStore();

const bulk = useBulkDelete<T>({
  deleteOne: props.deleteOne,
  refresh: props.refresh,
  entitySingular: props.entitySingular,
  entityPlural: props.entityPlural,
  getName: props.getName,
  getOwnerId: props.getOwnerId,
  recursive: props.recursive,
  recursiveLabel: props.recursiveLabel,
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
} = bulk;

const manageableItems = computed(() =>
  props.items.filter((item) => bulk.canManage(item)),
);

const hasManageable = computed(() => manageableItems.value.length > 0);

const allSelected = computed(() => bulk.allSelected(manageableItems.value));
const someSelected = computed(() => bulk.someSelected(manageableItems.value));

const searchPlaceholderText = computed(
  () =>
    props.searchPlaceholder ??
    t("browse.list.searchPlaceholder", { entity: props.entityPlural }),
);

const emptyText = computed(
  () =>
    props.emptyMessage ??
    t("browse.list.empty", { entity: props.entityPlural }),
);

const gridStyle = computed(() => ({
  gridTemplateColumns: `repeat(auto-fill, minmax(${props.gridMinWidth}, 1fr))`,
}));

function onSearch(q: string) {
  emit("update:query", q);
  void props.search(q);
}

function onLoadMore() {
  if (props.loading) return;
  void props.loadMore();
}

function onRetry() {
  void props.retry();
}

defineSlots<{
  "header-actions"?: (props: { bulkMode: boolean }) => unknown;
  card?: (props: { item: T; bulkMode: boolean }) => unknown;
  empty?: () => unknown;
}>();
</script>

<template>
  <div class="bulk-editable-grid">
    <div class="bulk-editable-grid__header">
      <AppPageTitle :icon="icon">{{ title }}</AppPageTitle>

      <div class="bulk-editable-grid__actions">
        <slot name="header-actions" :bulk-mode="bulkMode" />

        <template v-if="authStore.isAuthenticated && !bulkMode">
          <AppButton
            v-if="hasManageable && !loading"
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

    <SearchBar
      :model-value="query"
      :debounce="0"
      class="bulk-editable-grid__search"
      :placeholder="searchPlaceholderText"
      @update:model-value="onSearch"
    />

    <div
      v-if="loading && items.length === 0"
      :class="[
        'bulk-editable-grid__skeleton',
        `bulk-editable-grid__skeleton--${layout}`,
      ]"
      :style="layout === 'grid' ? gridStyle : undefined"
    >
      <SkeletonLoader
        v-for="i in layout === 'list' ? 5 : 8"
        :key="i"
        :variant="layout === 'list' ? 'list-row' : 'card'"
      />
    </div>

    <div v-else-if="error" class="bulk-editable-grid__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="onRetry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div v-else-if="items.length === 0" class="bulk-editable-grid__empty">
      <slot name="empty">
        {{ emptyText }}
      </slot>
    </div>

    <template v-else>
      <ul
        v-if="layout === 'list'"
        class="bulk-editable-grid__items bulk-editable-grid__items--list"
      >
        <li
          v-for="item in items"
          :key="item.id"
          :class="[
            'bulk-editable-grid__item-wrapper',
            'bulk-editable-grid__item-wrapper--list',
            itemClass,
            { 'bulk-editable-grid__item-wrapper--bulk': bulkMode },
          ]"
        >
          <slot name="card" :item="item" :bulk-mode="bulkMode" />

          <AppCheckbox
            v-if="bulkMode"
            class="bulk-editable-grid__item-checkbox"
            :model-value="selectedIds.has(item.id)"
            :disabled="!bulk.canManage(item)"
            @update:model-value="bulk.toggleSelect(item.id)"
          />

          <AppButton
            v-else-if="bulk.canManage(item)"
            class="bulk-editable-grid__item-delete"
            variant="danger"
            size="sm"
            icon="trash"
            :title="t('common.delete')"
            @click="bulk.openDeleteSingle(item)"
          />
        </li>
      </ul>

      <div
        v-else
        class="bulk-editable-grid__items bulk-editable-grid__items--grid"
        :style="gridStyle"
      >
        <div
          v-for="item in items"
          :key="item.id"
          :class="[
            'bulk-editable-grid__item-wrapper',
            'bulk-editable-grid__item-wrapper--grid',
            itemClass,
            { 'bulk-editable-grid__item-wrapper--bulk': bulkMode },
          ]"
        >
          <slot name="card" :item="item" :bulk-mode="bulkMode" />

          <AppCheckbox
            v-if="bulkMode"
            class="bulk-editable-grid__item-checkbox"
            :model-value="selectedIds.has(item.id)"
            :disabled="!bulk.canManage(item)"
            @update:model-value="bulk.toggleSelect(item.id)"
          />

          <AppButton
            v-else-if="bulk.canManage(item)"
            class="bulk-editable-grid__item-delete"
            variant="danger"
            size="sm"
            icon="trash"
            :title="t('common.delete')"
            @click="bulk.openDeleteSingle(item)"
          />
        </div>
      </div>
    </template>

    <div class="bulk-editable-grid__footer">
      <AppButton
        v-if="hasMore"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        icon="chevron-down"
        @click="onLoadMore"
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
      :recursive-label="bulk.recursiveLabel.value"
      :loading="deleteModalLoading"
      @close="bulk.closeDeleteModal"
      @confirm="bulk.confirmDelete"
    />
  </div>
</template>

<style scoped>
.bulk-editable-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.bulk-editable-grid__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.bulk-editable-grid__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}

@media (max-width: 767px) {
  .bulk-editable-grid__header {
    flex-direction: column;
    align-items: flex-start;
  }
}

.bulk-editable-grid__search {
  max-width: 32rem;
}

.bulk-editable-grid__skeleton--grid {
  display: grid;
  gap: var(--space-4);
}

.bulk-editable-grid__skeleton--list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.bulk-editable-grid__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.bulk-editable-grid__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.bulk-editable-grid__items--grid {
  display: grid;
  gap: var(--space-4);
}

.bulk-editable-grid__items--list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}

.bulk-editable-grid__item-wrapper {
  position: relative;
  padding: var(--space-3);
}

.bulk-editable-grid__item-wrapper:hover {
  background-color: var(--color-surface-raised);
}

.bulk-editable-grid__item-wrapper--list {
  display: flex;
  align-items: center;
  padding-right: 2.5rem;
}

.bulk-editable-grid__item-checkbox {
  position: absolute;
  top: calc(0.75 * var(--space-3));
  right: var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  padding: var(--space-1);
}

.bulk-editable-grid__item-delete {
  position: absolute;
  top: calc(0.75 * var(--space-3));
  right: var(--space-2);
  z-index: 1;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.bulk-editable-grid__item-wrapper:hover .bulk-editable-grid__item-delete,
.bulk-editable-grid__item-delete:focus-visible {
  opacity: 1;
}

.bulk-editable-grid__footer {
  display: flex;
  justify-content: center;
}
</style>
