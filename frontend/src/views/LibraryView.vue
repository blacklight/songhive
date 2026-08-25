<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import {
  listLibraries,
  createLibrary,
  deleteLibrary,
  type LibraryResponse,
  type LibraryCreate,
} from "@/api/libraries";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { useBulkDelete } from "@/composables/useBulkDelete";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import LibraryCard from "@/components/library/LibraryCard.vue";
import DeleteModal from "@/components/entity/DeleteModal.vue";
import type { Visibility } from "@/api/libraries";

const { t } = useI18n();
const authStore = useAuthStore();
const toastStore = useToastStore();
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
} = useEntityList<LibraryResponse>(listLibraries);

const bulk = useBulkDelete<LibraryResponse>({
  deleteOne: deleteLibrary,
  refresh,
  entitySingular: t("browse.entities.library"),
  entityPlural: t("browse.entities.libraries"),
  getName: (library) => library.name,
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

const manageableLibraries = computed(() =>
  items.value.filter((library) => bulk.canManage(library)),
);

const allSelected = computed(() => bulk.allSelected(manageableLibraries.value));
const someSelected = computed(() =>
  bulk.someSelected(manageableLibraries.value),
);

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
  const body: LibraryCreate = {
    name: name.value.trim(),
    description: description.value.trim() || null,
  };

  try {
    await createLibrary(body, { visibility: visibility.value });
    toastStore.push({ type: "success", message: t("browse.createLibrary") });
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
  <div class="library-view">
    <div class="library-view__header">
      <AppPageTitle class="library-view__title" icon="folder-open">
        {{ t("nav.libraries") }}
      </AppPageTitle>

      <div class="library-view__actions">
        <template v-if="authStore.isAuthenticated && !bulkMode">
          <AppButton v-if="canCreate" size="sm" icon="plus" @click="openCreate">
            {{ t("browse.list.createLibrary") }}
          </AppButton>
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
      class="library-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.libraries'),
        })
      "
      @update:model-value="search"
    />

    <div
      v-if="loading && items.length === 0"
      class="library-view__grid library-view__grid--skeleton"
    >
      <SkeletonLoader v-for="i in 8" :key="i" variant="card" />
    </div>

    <div v-else-if="error" class="library-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div v-else-if="items.length === 0" class="library-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.libraries") }) }}
    </div>

    <div
      v-else
      class="library-view__grid"
      :class="{ 'library-view__grid--bulk': bulkMode }"
    >
      <div
        v-for="library in items"
        :key="library.id"
        class="library-view__card-wrapper"
      >
        <LibraryCard
          class="library-view__card"
          :class="{
            'library-view__card--selectable': bulkMode,
          }"
          :library="library"
        />

        <AppCheckbox
          v-if="bulkMode"
          class="library-view__card-checkbox"
          :model-value="selectedIds.has(library.id)"
          :disabled="!bulk.canManage(library)"
          @update:model-value="bulk.toggleSelect(library.id)"
        />

        <AppButton
          v-else-if="bulk.canManage(library)"
          class="library-view__card-delete"
          variant="danger"
          size="sm"
          icon="trash"
          :title="t('common.delete')"
          @click="bulk.openDeleteSingle(library)"
        />
      </div>
    </div>

    <div class="library-view__footer">
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

    <AppModal
      :open="isCreateOpen"
      :title="t('browse.list.newLibrary')"
      @close="closeCreate"
    >
      <form
        id="create-library-form"
        class="library-view__create-form"
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
        <p v-if="createError" class="library-view__create-error" role="alert">
          {{ createError }}
        </p>
      </form>

      <template #actions>
        <AppButton variant="secondary" icon="xmark" @click="closeCreate">
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          form="create-library-form"
          type="submit"
          :loading="isCreating"
          :disabled="isCreating || !name.trim()"
          icon="floppy-disk"
        >
          {{ t("common.save") }}
        </AppButton>
      </template>
    </AppModal>

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
.library-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.library-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.library-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.library-view__actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.library-view__search {
  max-width: 32rem;
}

.library-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: var(--space-4);
}

.library-view__grid--bulk .library-view__card {
  pointer-events: none;
  opacity: 0.8;
}

.library-view__card-wrapper {
  position: relative;
}

.library-view__card {
  display: block;
}

.library-view__card-checkbox {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  padding: var(--space-1);
}

.library-view__card-delete {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  z-index: 1;
}

.library-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.library-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.library-view__footer {
  display: flex;
  justify-content: center;
}

.library-view__create-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.library-view__create-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
