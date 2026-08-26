<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import {
  listPlaylists,
  createPlaylist,
  deletePlaylist,
  type PlaylistResponse,
  type PlaylistCreate,
} from "@/api/playlists";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import PlaylistCard from "@/components/library/PlaylistCard.vue";
import BulkEditableGrid from "@/components/entity/BulkEditableGrid.vue";
import type { Visibility } from "@/api/playlists";

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
} = useEntityList<PlaylistResponse>(listPlaylists);

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
      :items="items"
      :loading="loading"
      :error="error"
      :has-more="hasMore"
      :query="query"
      :entity-singular="t('browse.entities.playlist')"
      :entity-plural="t('browse.entities.playlists')"
      :delete-one="deletePlaylist"
      :refresh="refresh"
      :get-name="(playlist) => playlist.name"
      :search="search"
      :load-more="loadMore"
      :retry="retry"
      :recursive="true"
      :recursive-label="
        t('browse.delete.recursive', { contents: t('browse.entities.tracks') })
      "
      grid-min-width="16rem"
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

      <template #card="{ item, bulkMode }">
        <PlaylistCard
          class="playlists-view__card"
          :class="{ 'playlists-view__card--selectable': bulkMode }"
          :playlist="item"
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
