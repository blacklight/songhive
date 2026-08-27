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
import AppButton from "@/components/ui/AppButton.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import LibraryCard from "@/components/library/LibraryCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";
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
  sortBy,
  sortDir,
  load,
  loadMore,
  search,
  setSort,
  retry,
  refresh,
} = useEntityList<LibraryResponse>(listLibraries, {
  defaultSortBy: "name",
  syncQuery: true,
});

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

function onSort(field: string, direction: "asc" | "desc") {
  void setSort(field, direction);
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
    <BulkEditableGrid
      :title="t('nav.libraries')"
      icon="folder-open"
      :items="items"
      :loading="loading"
      :error="error"
      :has-more="hasMore"
      :query="query"
      :entity-singular="t('browse.entities.library')"
      :entity-plural="t('browse.entities.libraries')"
      :delete-one="deleteLibrary"
      :refresh="refresh"
      :get-name="(library) => library.name"
      :search="search"
      :load-more="loadMore"
      :retry="retry"
      :sort-by="sortBy"
      :sort-dir="sortDir"
      :sort-options="sortOptions"
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
          {{ t("browse.list.createLibrary") }}
        </AppButton>
      </template>

      <template #card="{ item, bulkMode }">
        <LibraryCard
          class="library-view__card"
          :class="{ 'library-view__card--selectable': bulkMode }"
          :library="item"
        />
      </template>
    </BulkEditableGrid>

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
  </div>
</template>

<style scoped>
.library-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.library-view__card--selectable {
  pointer-events: none;
  opacity: 0.8;
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
