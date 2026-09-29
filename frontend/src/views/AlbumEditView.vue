<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  getAlbum,
  updateAlbum,
  deleteAlbum,
  uploadAlbumCover,
  deleteAlbumCover,
  type AlbumResponse,
  type AlbumUpdate,
} from "@/api/albums";
import { getApiErrorMessage } from "@/api/client";
import { useCanManage } from "@/composables/useCanManage";
import { useEntityTags } from "@/composables/useEntityTags";
import { useEntityGenres } from "@/composables/useEntityGenres";
import TagInput from "@/components/tags/TagInput.vue";
import GenreInput from "@/components/genres/GenreInput.vue";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import type { Visibility } from "@/api/libraries";
import { parseNumber, toVisibility } from "@/utils/entity";
import { isFieldEditable, isProviderManaged } from "@/utils/editableFields";
import { providerDisplayName } from "@/utils/providerName";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import ImageUploadField from "@/components/ui/ImageUploadField.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import SaleEditor from "@/components/payments/SaleEditor.vue";
import { useInstanceStore } from "@/stores/instance";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const toast = useToastStore();

const instanceStore = useInstanceStore();
const albumId = computed(() => String(route.params.id));
const paymentsEnabled = computed(() => instanceStore.paymentsEnabled);
const album = ref<AlbumResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const title = ref("");
const releaseYear = ref("");
const description = ref("");
const visibility = ref<Visibility>("private");
const isSaving = ref(false);
const isDeleting = ref(false);
const isUploadingCover = ref(false);
const isRemovingCover = ref(false);
const coverError = ref<string | null>(null);

const { canManage } = useCanManage(
  computed(() => album.value?.owner_id ?? null),
);

const editableFields = computed(() => album.value?.editable_fields ?? null);
const providerManaged = computed(() => isProviderManaged(editableFields.value));
const managedByLabel = computed(() =>
  providerDisplayName(album.value?.external_provider_type),
);
const fieldEditable = (cap: string) =>
  isFieldEditable(editableFields.value, cap);

const { tags, resetTags, syncTags } = useEntityTags();
const { genres, resetGenres, syncGenres } = useEntityGenres();

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

function resetForm() {
  title.value = album.value?.title ?? "";
  releaseYear.value =
    album.value?.release_year != null ? String(album.value.release_year) : "";
  description.value = album.value?.description ?? "";
  visibility.value = toVisibility(album.value?.visibility);
  resetTags(album.value?.tags ?? null);
  resetGenres(album.value?.genres ?? null);
  error.value = null;
}

async function loadAlbum() {
  loading.value = true;
  error.value = null;
  try {
    album.value = await getAlbum(albumId.value, { include: "tags,genres" });
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  album.value = null;
  await loadAlbum();
  if (!album.value) return;

  if (!canManage.value) {
    await router.replace(`/albums/${albumId.value}`);
    return;
  }

  resetForm();
}

async function onSubmit() {
  if (fieldEditable("title") && !title.value.trim()) return;

  isSaving.value = true;
  error.value = null;

  // Provider-managed fields are sent only when editable — the backend
  // rejects locked fields (422) merely for being present in the payload.
  const body: AlbumUpdate = {
    visibility: visibility.value,
  };
  if (fieldEditable("title")) body.title = title.value.trim();
  if (fieldEditable("release_year"))
    body.release_year = parseNumber(releaseYear.value);
  if (fieldEditable("description"))
    body.description = description.value.trim() || null;
  if (fieldEditable("genres")) body.genre = genres.value.join("; ") || null;

  try {
    await updateAlbum(albumId.value, body);
    if (fieldEditable("tags")) await syncTags("albums", albumId.value);
    if (fieldEditable("genres")) await syncGenres("albums", albumId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await router.push(`/albums/${albumId.value}`);
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
  if (!album.value) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.edit.deleteConfirm", { name: album.value.title }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    await deleteAlbum(albumId.value);
    toast.push({ type: "success", message: t("browse.edit.deleted") });
    await router.push("/albums");
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

async function refreshAlbum() {
  try {
    album.value = await getAlbum(albumId.value, { include: "tags,genres" });
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  }
}

async function onUploadCover(file: File) {
  if (!album.value) return;
  coverError.value = null;
  isUploadingCover.value = true;
  try {
    await uploadAlbumCover(albumId.value, file);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshAlbum();
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
  if (!album.value) return;
  coverError.value = null;
  isRemovingCover.value = true;
  try {
    await deleteAlbumCover(albumId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshAlbum();
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
  <div class="album-edit-view">
    <div v-if="loading && !album" class="album-edit-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="album-edit-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else-if="album && canManage">
      <AppPageTitle class="album-edit-view__title" icon="pen-to-square">
        {{ t("browse.edit.editAlbum") }}
      </AppPageTitle>

      <p
        v-if="providerManaged"
        class="album-edit-view__provider-note"
        role="note"
      >
        <i class="fa-solid fa-cloud" aria-hidden="true" />
        {{ t("browse.edit.providerManaged", { provider: managedByLabel }) }}
      </p>

      <form class="album-edit-view__form" @submit.prevent="onSubmit">
        <AppInput
          v-model="title"
          :label="t('browse.edit.title')"
          :required="true"
          :disabled="!fieldEditable('title')"
        />
        <AppInput
          v-model="releaseYear"
          type="number"
          :label="t('browse.edit.releaseYear')"
          :disabled="!fieldEditable('release_year')"
        />
        <AppInput
          v-model="description"
          as="textarea"
          :label="t('browse.edit.description')"
          :disabled="!fieldEditable('description')"
        />
        <GenreInput
          v-if="canManage"
          v-model="genres"
          :placeholder="t('genres.placeholder')"
          :aria-label="t('genres.ariaLabel')"
          :disabled="!fieldEditable('genres')"
        />
        <AppSelect
          v-model="visibility"
          :label="t('browse.detail.visibility')"
          :options="visibilityOptions"
        />

        <TagInput
          v-if="canManage"
          v-model="tags"
          :placeholder="t('tags.placeholder')"
          :aria-label="t('tags.label')"
          :disabled="!fieldEditable('tags')"
        />

        <div class="album-edit-view__actions">
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

      <SaleEditor
        v-if="paymentsEnabled"
        entity-type="album"
        :entity-id="albumId"
      />

      <section
        class="album-edit-view__section"
        aria-labelledby="album-cover-heading"
      >
        <AppPageTitle
          id="album-cover-heading"
          :level="2"
          class="album-edit-view__section-title"
          icon="image"
        >
          {{ "Cover art" }}
        </AppPageTitle>

        <ImageUploadField
          :label="'Cover art'"
          :image-url="album.cover_url"
          :upload-label="'Upload cover'"
          :remove-label="'Remove cover'"
          accept="image/*"
          :loading="isUploadingCover"
          :removing="isRemovingCover"
          :disabled="!fieldEditable('image')"
          :error="coverError ?? undefined"
          @upload="onUploadCover"
          @remove="onRemoveCover"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.album-edit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 48rem;
}

.album-edit-view__skeleton {
  min-height: 16rem;
}

.album-edit-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.album-edit-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.album-edit-view__provider-note {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.album-edit-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.album-edit-view__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}

.album-edit-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.album-edit-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}
</style>
