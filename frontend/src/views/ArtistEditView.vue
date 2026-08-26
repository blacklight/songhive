<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  getArtist,
  updateArtist,
  deleteArtist,
  uploadArtistImage,
  deleteArtistImage,
  uploadArtistCover,
  deleteArtistCover,
  type ArtistResponse,
  type ArtistUpdate,
} from "@/api/artists";
import { getApiErrorMessage } from "@/api/client";
import { useOwnership } from "@/composables/useOwnership";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import ImageUploadField from "@/components/ui/ImageUploadField.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const toast = useToastStore();
const authStore = useAuthStore();

const artistId = computed(() => String(route.params.id));
const artist = ref<ArtistResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const name = ref("");
const bio = ref("");
const isSaving = ref(false);
const isDeleting = ref(false);
const isUploadingImage = ref(false);
const isRemovingImage = ref(false);
const isUploadingCover = ref(false);
const isRemovingCover = ref(false);
const imageError = ref<string | null>(null);
const coverError = ref<string | null>(null);

const { isOwner } = useOwnership(
  computed(() => (authStore.isAdmin ? (authStore.user?.id ?? null) : null)),
);

function resetForm() {
  name.value = artist.value?.name ?? "";
  bio.value = artist.value?.bio ?? "";
  error.value = null;
}

async function loadArtist() {
  loading.value = true;
  error.value = null;
  try {
    artist.value = await getArtist(artistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  artist.value = null;
  await loadArtist();
  if (!artist.value) return;

  if (!isOwner.value) {
    await router.replace(`/artists/${artistId.value}`);
    return;
  }

  resetForm();
}

async function onSubmit() {
  if (!name.value.trim()) return;

  isSaving.value = true;
  error.value = null;

  const body: ArtistUpdate = {
    name: name.value.trim(),
    bio: bio.value.trim() || null,
  };

  try {
    await updateArtist(artistId.value, body);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await router.push(`/artists/${artistId.value}`);
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
  if (!artist.value) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.edit.deleteConfirm", { name: artist.value.name }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    await deleteArtist(artistId.value);
    toast.push({ type: "success", message: t("browse.edit.deleted") });
    await router.push("/artists");
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

async function refreshArtist() {
  try {
    artist.value = await getArtist(artistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  }
}

async function onUploadImage(file: File) {
  if (!artist.value) return;
  imageError.value = null;
  isUploadingImage.value = true;
  try {
    await uploadArtistImage(artistId.value, file);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshArtist();
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
  if (!artist.value) return;
  imageError.value = null;
  isRemovingImage.value = true;
  try {
    await deleteArtistImage(artistId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshArtist();
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
  if (!artist.value) return;
  coverError.value = null;
  isUploadingCover.value = true;
  try {
    await uploadArtistCover(artistId.value, file);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshArtist();
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
  if (!artist.value) return;
  coverError.value = null;
  isRemovingCover.value = true;
  try {
    await deleteArtistCover(artistId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshArtist();
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
  <div class="artist-edit-view">
    <div v-if="loading && !artist" class="artist-edit-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="artist-edit-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else-if="artist && isOwner">
      <AppPageTitle class="artist-edit-view__title" icon="pen-to-square">
        {{ "Edit artist" }}
      </AppPageTitle>

      <form class="artist-edit-view__form" @submit.prevent="onSubmit">
        <AppInput
          v-model="name"
          :label="t('browse.edit.name')"
          :required="true"
        />
        <AppInput v-model="bio" as="textarea" :label="'Bio'" :rows="6" />

        <div class="artist-edit-view__actions">
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
        class="artist-edit-view__section"
        aria-labelledby="artist-images-heading"
      >
        <AppPageTitle
          id="artist-images-heading"
          :level="2"
          class="artist-edit-view__section-title"
          icon="image"
        >
          {{ "Images" }}
        </AppPageTitle>

        <div class="artist-edit-view__image-fields">
          <ImageUploadField
            :label="'Artist image'"
            :image-url="artist.image_url"
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
            :label="'Artist cover'"
            :image-url="artist.cover_url"
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
.artist-edit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 48rem;
}

.artist-edit-view__skeleton {
  min-height: 16rem;
}

.artist-edit-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.artist-edit-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.artist-edit-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artist-edit-view__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}

.artist-edit-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.artist-edit-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.artist-edit-view__image-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-4);
  align-items: start;
}
</style>
