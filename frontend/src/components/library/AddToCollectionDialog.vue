<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { ApiError, getApiErrorMessage } from "@/api/client";
import {
  listLibraries,
  createLibrary,
  addTracksToLibrary,
  type LibraryResponse,
  type LibraryCreate,
  type Visibility,
} from "@/api/libraries";
import {
  listPlaylists,
  createPlaylist,
  addTracksToPlaylist,
  type PlaylistResponse,
  type PlaylistCreate,
} from "@/api/playlists";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

export type CollectionMode = "library" | "playlist";
export type AddableItemType = "track" | "album" | "artist";

export interface Props {
  open: boolean;
  mode: CollectionMode;
  itemType: AddableItemType;
  itemId: string;
  itemName?: string;
}

const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const { t } = useI18n();
const authStore = useAuthStore();
const toastStore = useToastStore();

const collections = ref<LibraryResponse[] | PlaylistResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const selectedId = ref<string>("");
const newName = ref("");
const isSaving = ref(false);
const saveError = ref<string | null>(null);
const showDuplicateWarning = ref(false);
const allowDuplicates = ref(false);

const isNew = computed(() => selectedId.value === "__new__");
const canCreate = computed(() => authStore.isAuthenticated);

function reset() {
  collections.value = [];
  loading.value = false;
  error.value = null;
  selectedId.value = "";
  newName.value = "";
  isSaving.value = false;
  saveError.value = null;
  showDuplicateWarning.value = false;
  allowDuplicates.value = false;
}

const title = computed(() => {
  const key =
    props.mode === "library"
      ? "browse.addToCollection.libraryTitle"
      : "browse.addToCollection.playlistTitle";
  return t(key, { name: props.itemName || t("browse.entities.item") });
});

function userCanAddToLibrary(lib: LibraryResponse) {
  return lib.can_write;
}

function userCanAddToPlaylist(playlist: PlaylistResponse) {
  return authStore.isAdmin || playlist.owner_id === authStore.user?.id;
}

const filteredCollections = computed(() => {
  if (props.mode === "library") {
    return (collections.value as LibraryResponse[]).filter(userCanAddToLibrary);
  }
  return (collections.value as PlaylistResponse[]).filter(userCanAddToPlaylist);
});

const options = computed(() => {
  const items = filteredCollections.value.map((c) => ({
    value: c.id,
    label: c.name,
  }));
  if (canCreate.value) {
    items.push({
      value: "__new__",
      label:
        props.mode === "library"
          ? t("browse.addToCollection.newLibrary")
          : t("browse.addToCollection.newPlaylist"),
    });
  }
  return items;
});

function selectFirst() {
  if (options.value.length > 0) {
    selectedId.value = options.value[0].value;
  }
}

async function loadCollections() {
  loading.value = true;
  error.value = null;
  try {
    if (props.mode === "library") {
      collections.value = await listLibraries({ limit: 100 });
    } else {
      collections.value = await listPlaylists({ limit: 100 });
    }
    selectFirst();
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      reset();
      loadCollections();
    }
  },
  { immediate: true },
);

watch(selectedId, () => {
  showDuplicateWarning.value = false;
  allowDuplicates.value = false;
});

function close() {
  if (!isSaving.value) emit("close");
}

function buildRequestBody():
  | { track_ids: string[]; allow_duplicates?: boolean }
  | { album_id: string; allow_duplicates?: boolean }
  | { artist_id: string; allow_duplicates?: boolean } {
  if (props.itemType === "track") {
    const body: { track_ids: string[]; allow_duplicates?: boolean } = {
      track_ids: [props.itemId],
    };
    if (props.mode === "playlist" && allowDuplicates.value) {
      body.allow_duplicates = true;
    }
    return body;
  }
  if (props.itemType === "album") {
    const body: { album_id: string; allow_duplicates?: boolean } = {
      album_id: props.itemId,
    };
    if (props.mode === "playlist" && allowDuplicates.value) {
      body.allow_duplicates = true;
    }
    return body;
  }
  const body: { artist_id: string; allow_duplicates?: boolean } = {
    artist_id: props.itemId,
  };
  if (props.mode === "playlist" && allowDuplicates.value) {
    body.allow_duplicates = true;
  }
  return body;
}

async function createCollection(): Promise<string> {
  const name = newName.value.trim();
  if (!name) throw new Error(t("browse.addToCollection.nameRequired"));
  const visibility: Visibility = "private";
  if (props.mode === "library") {
    const created = await createLibrary(
      { name, description: null } as LibraryCreate,
      { visibility },
    );
    return created.id;
  }
  const created = await createPlaylist(
    { name, description: null } as PlaylistCreate,
    { visibility },
  );
  return created.id;
}

const confirmLabel = computed(() => {
  return showDuplicateWarning.value
    ? t("browse.addToCollection.addAnyway")
    : t("common.save");
});

async function onConfirm() {
  saveError.value = null;
  if (isNew.value && !newName.value.trim()) {
    saveError.value = t("browse.addToCollection.nameRequired");
    return;
  }
  if (showDuplicateWarning.value && !allowDuplicates.value) {
    return;
  }
  isSaving.value = true;
  try {
    const targetId = isNew.value ? await createCollection() : selectedId.value;
    const body = buildRequestBody();
    const response =
      props.mode === "library"
        ? await addTracksToLibrary(targetId, body)
        : await addTracksToPlaylist(targetId, body);
    if (response.added === 0) {
      toastStore.push({
        type: "info",
        message: t("browse.addToCollection.noneAdded"),
      });
    } else {
      toastStore.push({
        type: "success",
        message:
          props.mode === "library"
            ? t("browse.addToCollection.librarySuccess", {
                name: props.itemName || t("browse.entities.item"),
                count: response.added,
              })
            : t("browse.addToCollection.playlistSuccess", {
                name: props.itemName || t("browse.entities.item"),
                count: response.added,
              }),
      });
    }
    emit("close");
  } catch (err) {
    if (
      err instanceof ApiError &&
      err.status === 409 &&
      props.mode === "playlist"
    ) {
      showDuplicateWarning.value = true;
    } else {
      showDuplicateWarning.value = false;
      allowDuplicates.value = false;
      saveError.value =
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown"));
    }
  } finally {
    isSaving.value = false;
  }
}
</script>

<template>
  <AppModal :open="props.open" :title="title" @close="close">
    <div class="add-to-collection">
      <div v-if="loading" class="add-to-collection__loading">
        <AppSpinner />
      </div>
      <div v-else-if="error" class="add-to-collection__error" role="alert">
        {{ error }}
      </div>
      <template v-else>
        <AppSelect
          v-if="options.length > 0"
          v-model="selectedId"
          :label="
            props.mode === 'library'
              ? t('browse.addToCollection.selectLibrary')
              : t('browse.addToCollection.selectPlaylist')
          "
          :options="options"
          :disabled="isSaving"
        />
        <p v-else class="add-to-collection__empty" role="alert">
          {{ t("browse.addToCollection.empty") }}
        </p>
        <template v-if="isNew">
          <AppInput
            v-model="newName"
            :label="t('browse.addToCollection.nameLabel')"
            :required="true"
            :disabled="isSaving"
          />
        </template>
        <p
          v-if="showDuplicateWarning"
          class="add-to-collection__warning"
          role="alert"
        >
          {{ t("browse.addToCollection.duplicateWarning") }}
        </p>
        <AppCheckbox
          v-if="showDuplicateWarning"
          v-model="allowDuplicates"
          :label="t('browse.addToCollection.addAnyway')"
        />
        <p v-if="saveError" class="add-to-collection__error" role="alert">
          {{ saveError }}
        </p>
      </template>
    </div>
    <template #actions>
      <AppButton
        variant="secondary"
        icon="xmark"
        :disabled="isSaving"
        @click="close"
      >
        {{ t("common.cancel") }}
      </AppButton>
      <AppButton
        variant="primary"
        icon="plus"
        :loading="isSaving"
        :disabled="
          isSaving ||
          (!isNew && !selectedId) ||
          (isNew && !newName.trim()) ||
          (showDuplicateWarning && !allowDuplicates)
        "
        @click="onConfirm"
      >
        {{ confirmLabel }}
      </AppButton>
    </template>
  </AppModal>
</template>

<style scoped>
.add-to-collection {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.add-to-collection__loading,
.add-to-collection__error,
.add-to-collection__empty {
  display: flex;
  justify-content: center;
  padding: var(--space-4);
}

.add-to-collection__error {
  color: var(--color-danger);
  justify-content: flex-start;
}

.add-to-collection__warning {
  color: var(--color-warning);
  margin: 0;
}

.add-to-collection__empty {
  color: var(--color-text-muted);
}
</style>
