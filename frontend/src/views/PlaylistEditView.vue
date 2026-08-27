<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  getPlaylist,
  updatePlaylist,
  deletePlaylist,
  uploadPlaylistImage,
  deletePlaylistImage,
  uploadPlaylistCover,
  deletePlaylistCover,
  type PlaylistResponse,
  type PlaylistUpdate,
  type Visibility,
} from "@/api/playlists";
import { getApiErrorMessage } from "@/api/client";
import { useCanManage } from "@/composables/useCanManage";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import { toVisibility } from "@/utils/entity";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import ImageUploadField from "@/components/ui/ImageUploadField.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const toast = useToastStore();

const playlistId = computed(() => String(route.params.id));
const playlist = ref<PlaylistResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const name = ref("");
const description = ref("");
const visibility = ref<Visibility>("private");
const isSaving = ref(false);
const isDeleting = ref(false);
const isUploadingImage = ref(false);
const isUploadingCover = ref(false);
const isRemovingImage = ref(false);
const isRemovingCover = ref(false);
const imageError = ref<string | null>(null);
const coverError = ref<string | null>(null);

const { canManage } = useCanManage(
  computed(() => playlist.value?.owner_id ?? null),
);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

function resetForm() {
  name.value = playlist.value?.name ?? "";
  description.value = playlist.value?.description ?? "";
  visibility.value = toVisibility(playlist.value?.visibility);
  error.value = null;
}

async function loadPlaylist() {
  loading.value = true;
  error.value = null;
  try {
    playlist.value = await getPlaylist(playlistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  playlist.value = null;
  await loadPlaylist();
  if (!playlist.value) return;

  if (!canManage.value) {
    await router.replace(`/playlists/${playlistId.value}`);
    return;
  }

  resetForm();
}

async function onSubmit() {
  if (!name.value.trim()) return;

  isSaving.value = true;
  error.value = null;

  const body: PlaylistUpdate = {
    name: name.value.trim(),
    description: description.value.trim() || null,
    visibility: visibility.value,
  };

  try {
    await updatePlaylist(playlistId.value, body);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await router.push(`/playlists/${playlistId.value}`);
  } catch (err) {
    error.value = t("browse.edit.saveError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isSaving.value = false;
  }
}

async function onDelete() {
  if (!playlist.value) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.edit.deleteConfirm", { name: playlist.value.name }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    await deletePlaylist(playlistId.value);
    toast.push({ type: "success", message: t("browse.edit.deleted") });
    await router.push("/playlists");
  } catch (err) {
    error.value = t("browse.edit.saveError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isDeleting.value = false;
  }
}

async function refreshPlaylist() {
  try {
    playlist.value = await getPlaylist(playlistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  }
}

async function onUploadImage(file: File) {
  if (!playlist.value) return;
  imageError.value = null;
  isUploadingImage.value = true;
  try {
    await uploadPlaylistImage(playlistId.value, file);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshPlaylist();
  } catch (err) {
    imageError.value = t("browse.libraryManagement.uploadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isUploadingImage.value = false;
  }
}

async function onRemoveImage() {
  if (!playlist.value) return;
  imageError.value = null;
  isRemovingImage.value = true;
  try {
    await deletePlaylistImage(playlistId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshPlaylist();
  } catch (err) {
    imageError.value = t("browse.libraryManagement.uploadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isRemovingImage.value = false;
  }
}

async function onUploadCover(file: File) {
  if (!playlist.value) return;
  coverError.value = null;
  isUploadingCover.value = true;
  try {
    await uploadPlaylistCover(playlistId.value, file);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshPlaylist();
  } catch (err) {
    coverError.value = t("browse.libraryManagement.uploadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isUploadingCover.value = false;
  }
}

async function onRemoveCover() {
  if (!playlist.value) return;
  coverError.value = null;
  isRemovingCover.value = true;
  try {
    await deletePlaylistCover(playlistId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshPlaylist();
  } catch (err) {
    coverError.value = t("browse.libraryManagement.uploadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isRemovingCover.value = false;
  }
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="playlist-edit-view">
    <div v-if="loading && !playlist" class="playlist-edit-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="playlist-edit-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else-if="playlist && canManage">
      <AppPageTitle class="playlist-edit-view__title" icon="pen-to-square">
        {{ "Edit playlist" }}
      </AppPageTitle>

      <form class="playlist-edit-view__form" @submit.prevent="onSubmit">
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

        <div class="playlist-edit-view__actions">
          <AppButton type="submit" :loading="isSaving" icon="floppy-disk">
            {{ t("common.save") }}
          </AppButton>
          <AppButton
            type="button"
            variant="danger"
            :loading="isDeleting"
            icon="trash-can"
            @click="onDelete"
          >
            {{ t("common.delete") }}
          </AppButton>
        </div>
      </form>

      <section
        class="playlist-edit-view__section"
        aria-labelledby="playlist-images-heading"
      >
        <AppPageTitle
          id="playlist-images-heading"
          :level="2"
          class="playlist-edit-view__section-title"
          icon="image"
        >
          {{ "Images" }}
        </AppPageTitle>

        <div class="playlist-edit-view__image-fields">
          <ImageUploadField
            :label="'Playlist image'"
            :image-url="playlist.image_url"
            :upload-label="'Upload image'"
            :remove-label="'Remove image'"
            accept="image/*"
            :loading="isUploadingImage"
            :removing="isRemovingImage"
            :error="imageError ?? undefined"
            @upload="onUploadImage"
            @remove="onRemoveImage"
          />
          <ImageUploadField
            :label="'Playlist cover'"
            :image-url="playlist.cover_url"
            :upload-label="'Upload cover'"
            :remove-label="'Remove cover'"
            accept="image/*"
            :loading="isUploadingCover"
            :removing="isRemovingCover"
            :error="coverError ?? undefined"
            @upload="onUploadCover"
            @remove="onRemoveCover"
          />
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.playlist-edit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 48rem;
}

.playlist-edit-view__skeleton {
  min-height: 16rem;
}

.playlist-edit-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.playlist-edit-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.playlist-edit-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.playlist-edit-view__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}

.playlist-edit-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.playlist-edit-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.playlist-edit-view__image-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-4);
  align-items: start;
}
</style>
