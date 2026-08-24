<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import {
  listPlaylists,
  createPlaylist,
  type PlaylistResponse,
  type PlaylistCreate,
} from "@/api/playlists";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import SearchBar from "@/components/ui/SearchBar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import PlaylistCard from "@/components/library/PlaylistCard.vue";
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
    <div class="playlists-view__header">
      <h1 class="playlists-view__title">{{ t("nav.playlists") }}</h1>
      <AppButton v-if="canCreate" size="sm" @click="openCreate">
        {{ t("browse.list.createPlaylist") }}
      </AppButton>
    </div>

    <SearchBar
      :model-value="query"
      :debounce="0"
      class="playlists-view__search"
      :placeholder="
        t('browse.list.searchPlaceholder', {
          entity: t('browse.entities.playlists'),
        })
      "
      @update:model-value="search"
    />

    <div
      v-if="loading && items.length === 0"
      class="playlists-view__grid playlists-view__grid--skeleton"
    >
      <SkeletonLoader v-for="i in 8" :key="i" variant="card" />
    </div>

    <div v-else-if="error" class="playlists-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="retry">{{ t("common.retry") }}</AppButton>
    </div>

    <div v-else-if="items.length === 0" class="playlists-view__empty">
      {{ t("browse.list.empty", { entity: t("browse.entities.playlists") }) }}
    </div>

    <div v-else class="playlists-view__grid">
      <PlaylistCard
        v-for="playlist in items"
        :key="playlist.id"
        :playlist="playlist"
      />
    </div>

    <div class="playlists-view__footer">
      <AppButton
        v-if="hasMore"
        variant="secondary"
        :loading="loading"
        :disabled="loading"
        @click="loadMore"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
      <AppSpinner v-else-if="loading" />
    </div>

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
        <AppButton variant="secondary" @click="closeCreate">
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          form="create-playlist-form"
          type="submit"
          :loading="isCreating"
          :disabled="isCreating || !name.trim()"
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

.playlists-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.playlists-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.playlists-view__search {
  max-width: 32rem;
}

.playlists-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: var(--space-4);
}

.playlists-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.playlists-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.playlists-view__footer {
  display: flex;
  justify-content: center;
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
