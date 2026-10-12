<script setup lang="ts" generic="T extends ManageableItem">
import { computed, nextTick, type ComponentPublicInstance } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import {
  useBulkDelete,
  type ManageableItem,
} from "@/composables/useBulkDelete";
import { indexLetterOf } from "@/utils/indexLetters";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AlphabetIndex from "@/components/ui/AlphabetIndex.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";
import SortControl, {
  type Option as SortOption,
} from "@/components/ui/SortControl.vue";

export interface Props<T extends ManageableItem> {
  title: string;
  icon?: string;
  /** Total entities matching the current filters, shown next to the title. */
  total?: number;
  items: T[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  /** Whether the loaded window has earlier items; shows "Load previous". */
  hasPrevious?: boolean;
  loadingPrevious?: boolean;
  query: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  entitySingular: string;
  entityPlural: string;
  deleteOne: (id: string, recursive: boolean) => Promise<void>;
  refresh: () => Promise<void> | void;
  getName: (item: T) => string;
  getOwnerId?: (item: T) => string | null | undefined;
  /** Extra gate on top of the owner/admin check for bulk management. */
  canManage?: (item: T) => boolean;
  recursive?: boolean;
  recursiveLabel?: string;
  layout?: "grid" | "list";
  gridMinWidth?: string;
  itemClass?: string;
  search: (q: string) => void | Promise<void>;
  loadMore: () => void | Promise<void>;
  loadPrevious?: () => void | Promise<void>;
  retry: () => void | Promise<void>;
  sortBy?: string;
  sortDir?: "asc" | "desc";
  sortOptions?: SortOption[];
  /**
   * Sort key used to bucket items under A–Z index letters, shown as an
   * alphabet scrollbar along the edge. Pass ``undefined`` when the
   * active sort is not alphabetical — the bar is hidden.
   */
  getIndexKey?: (item: T) => string | number | null | undefined;
  /**
   * Letters enabled in the index, typically from the backend initials
   * endpoint. ``undefined`` falls back to letters found in ``items`` —
   * pass an empty set while the initials response is still pending if
   * you only want backend-provided letters enabled.
   */
  indexAvailable?: Set<string>;
  /**
   * Async hook run before scrolling to a letter's anchor; use it to
   * fetch the fragment containing the letter when it isn't loaded yet.
   */
  resolveIndexJump?: (letter: string) => void | Promise<void>;
}

const props = withDefaults(defineProps<Props<T>>(), {
  icon: undefined,
  total: undefined,
  searchPlaceholder: undefined,
  emptyMessage: undefined,
  getOwnerId: undefined,
  canManage: undefined,
  recursive: false,
  recursiveLabel: undefined,
  layout: "grid",
  gridMinWidth: "12rem",
  itemClass: undefined,
  sortBy: undefined,
  sortDir: undefined,
  sortOptions: undefined,
  getIndexKey: undefined,
  hasPrevious: false,
  loadingPrevious: false,
  loadPrevious: undefined,
  indexAvailable: undefined,
  resolveIndexJump: undefined,
});

const emit = defineEmits<{
  "update:query": [value: string];
  sort: [field: string, direction: "asc" | "desc"];
}>();

const { t } = useI18n();
const authStore = useAuthStore();

const firstSortOption = computed(() =>
  props.sortOptions && props.sortOptions.length > 0
    ? props.sortOptions[0]
    : undefined,
);

const currentSortField = computed(
  () => props.sortBy ?? firstSortOption.value?.value ?? "",
);

const currentSortDir = computed(() => props.sortDir ?? "asc");

const bulk = useBulkDelete<T>({
  deleteOne: props.deleteOne,
  refresh: props.refresh,
  entitySingular: props.entitySingular,
  entityPlural: props.entityPlural,
  getName: props.getName,
  getOwnerId: props.getOwnerId,
  canManage: props.canManage,
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

function onLoadPrevious() {
  if (props.loading) return;
  void props.loadPrevious?.();
}

function onRetry() {
  void props.retry();
}

function onSort(field: string, direction: "asc" | "desc") {
  emit("sort", field, direction);
}

const itemEls = new Map<string, HTMLElement>();

function setItemEl(id: string, el: Element | ComponentPublicInstance | null) {
  if (el instanceof HTMLElement) itemEls.set(id, el);
  else itemEls.delete(id);
}

// First item under each index letter, in display order.
const letterAnchors = computed(() => {
  const anchors = new Map<string, string>();
  if (!props.getIndexKey) return anchors;
  for (const item of props.items) {
    const letter = indexLetterOf(String(props.getIndexKey(item) ?? ""));
    if (!anchors.has(letter)) anchors.set(letter, item.id);
  }
  return anchors;
});

// Backend-provided letters plus any found in the loaded items (remote
// entities can introduce letters the local initials don't cover).
const availableLetters = computed(() => {
  const letters = new Set<string>(letterAnchors.value.keys());
  if (props.indexAvailable) {
    for (const letter of props.indexAvailable) letters.add(letter);
  }
  return letters;
});

async function onIndexJump(letter: string) {
  await props.resolveIndexJump?.(letter);
  await nextTick();
  const id = letterAnchors.value.get(letter);
  const el = id ? itemEls.get(id) : undefined;
  el?.scrollIntoView({ behavior: "smooth", block: "start" });
}

defineSlots<{
  "header-actions"?: (props: { bulkMode: boolean }) => unknown;
  filters?: () => unknown;
  card?: (props: { item: T; bulkMode: boolean }) => unknown;
  empty?: () => unknown;
}>();
</script>

<template>
  <div class="bulk-editable-grid">
    <div class="bulk-editable-grid__header">
      <AppPageTitle :icon="icon">
        {{ title }}
        <span
          v-if="total !== undefined && (total > 0 || !loading)"
          class="bulk-editable-grid__count"
          >({{ total }})</span
        >
      </AppPageTitle>

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

    <div
      v-if="firstSortOption || $slots.filters"
      class="bulk-editable-grid__sort"
    >
      <SortControl
        v-if="firstSortOption"
        :model-value="currentSortField"
        :direction="currentSortDir"
        :options="props.sortOptions ?? []"
        @update:model-value="(field) => onSort(field, currentSortDir)"
        @update:direction="(dir) => onSort(currentSortField, dir)"
      />
      <slot name="filters" />
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
      <AlphabetIndex
        v-if="getIndexKey"
        :available="availableLetters"
        @jump="onIndexJump"
      />

      <div v-if="hasPrevious" class="bulk-editable-grid__load-previous">
        <AppButton
          variant="secondary"
          :loading="loadingPrevious"
          :disabled="loading"
          icon="chevron-up"
          @click="onLoadPrevious"
        >
          {{ t("browse.list.loadPrevious") }}
        </AppButton>
      </div>

      <ul
        v-if="layout === 'list'"
        class="bulk-editable-grid__items bulk-editable-grid__items--list"
      >
        <li
          v-for="item in items"
          :key="item.id"
          :ref="(el) => setItemEl(item.id, el)"
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
          :ref="(el) => setItemEl(item.id, el)"
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

.bulk-editable-grid__count {
  font-weight: normal;
  color: var(--color-text-muted);
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

.bulk-editable-grid__sort {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-start;
  gap: var(--space-3);
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
  /* Keep the jump target clear of the sticky topbar. */
  scroll-margin-top: var(--header-height);
}

.bulk-editable-grid__item-wrapper:hover {
  background-color: var(--color-surface-hover);
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

.bulk-editable-grid__load-previous,
.bulk-editable-grid__footer {
  display: flex;
  justify-content: center;
}
</style>
