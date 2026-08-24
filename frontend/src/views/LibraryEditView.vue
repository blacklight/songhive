<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  useEntityList,
  type EntityListParams,
} from "@/composables/useEntityList";
import {
  getLibrary,
  updateLibrary,
  deleteLibrary,
  listLibraryTracks,
  uploadTrack,
  bulkUploadTracks,
  scanLibrary,
  type LibraryResponse,
  type LibraryUpdate,
  type Visibility,
} from "@/api/libraries";
import type { TrackResponse } from "@/api/tracks";
import { toVisibility } from "@/utils/entity";
import { getApiErrorMessage } from "@/api/client";
import { useOwnership } from "@/composables/useOwnership";
import { useTrackEnrichment } from "@/composables/useTrackEnrichment";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackList from "@/components/library/TrackList.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const toast = useToastStore();

const libraryId = computed(() => String(route.params.id));
const library = ref<LibraryResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const name = ref("");
const description = ref("");
const visibility = ref<Visibility>("private");
const isSaving = ref(false);
const isDeleting = ref(false);

const uploadVisibility = ref<Visibility>("private");
const uploadForce = ref(false);
const uploadEnrich = ref(true);
const isUploading = ref(false);
const isBulkUploading = ref(false);
const uploadError = ref<string | null>(null);
const singleFileInput = ref<HTMLInputElement | null>(null);
const bulkFileInput = ref<HTMLInputElement | null>(null);

const scanPath = ref("");
const isScanning = ref(false);
const scanError = ref<string | null>(null);

const { isOwner } = useOwnership(
  computed(() => library.value?.owner_id ?? null),
);

const {
  items: tracks,
  loading: tracksLoading,
  error: tracksError,
  hasMore: tracksHasMore,
  load: loadTracks,
  loadMore: loadMoreTracks,
  retry: retryTracks,
} = useEntityList<TrackResponse>((params: EntityListParams) =>
  listLibraryTracks(libraryId.value, {
    limit: params.limit,
    offset: params.offset,
  }),
);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

const { enrich: trackEnrich } = useTrackEnrichment(
  tracks,
  computed(() => library.value?.name ?? ""),
);

function resetForm() {
  name.value = library.value?.name ?? "";
  description.value = library.value?.description ?? "";
  visibility.value = toVisibility(library.value?.visibility);
  error.value = null;
}

async function loadLibrary() {
  loading.value = true;
  error.value = null;
  try {
    library.value = await getLibrary(libraryId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  library.value = null;
  await loadLibrary();
  if (!library.value) return;

  if (!isOwner.value) {
    await router.replace(`/libraries/${libraryId.value}`);
    return;
  }

  resetForm();
  await loadTracks(true);
}

async function onSubmit() {
  if (!name.value.trim()) return;

  isSaving.value = true;
  error.value = null;

  const body: LibraryUpdate = {
    name: name.value.trim(),
    description: description.value.trim() || null,
    visibility: visibility.value,
  };

  try {
    await updateLibrary(libraryId.value, body);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await router.push(`/libraries/${libraryId.value}`);
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
  if (!library.value) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.edit.deleteConfirm", { name: library.value.name }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    await deleteLibrary(libraryId.value);
    toast.push({ type: "success", message: t("browse.edit.deleted") });
    await router.push("/libraries");
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

function clearUploadError() {
  uploadError.value = null;
}

async function onSingleFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file || !library.value) {
    if (target) target.value = "";
    return;
  }

  clearUploadError();
  isUploading.value = true;
  try {
    await uploadTrack(libraryId.value, file, {
      visibility: uploadVisibility.value,
      force: uploadForce.value,
      enrich: uploadEnrich.value,
    });
    toast.push({
      type: "success",
      message: t("browse.libraryManagement.uploadSuccess"),
    });
    await loadTracks(true);
  } catch (err) {
    uploadError.value = t("browse.libraryManagement.uploadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isUploading.value = false;
    if (target) target.value = "";
  }
}

async function onBulkFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const files = target.files ? Array.from(target.files) : [];
  if (files.length === 0 || !library.value) {
    if (target) target.value = "";
    return;
  }

  clearUploadError();
  isBulkUploading.value = true;
  try {
    await bulkUploadTracks(libraryId.value, files, {
      visibility: uploadVisibility.value,
      force: uploadForce.value,
      enrich: uploadEnrich.value,
    });
    toast.push({
      type: "success",
      message: t("browse.libraryManagement.uploadSuccess"),
    });
    await loadTracks(true);
  } catch (err) {
    uploadError.value = t("browse.libraryManagement.uploadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isBulkUploading.value = false;
    if (target) target.value = "";
  }
}

async function onScan() {
  if (!scanPath.value.trim() || !library.value) return;

  scanError.value = null;
  isScanning.value = true;
  try {
    await scanLibrary(libraryId.value, { path: scanPath.value.trim() });
    toast.push({
      type: "success",
      message: t("browse.libraryManagement.scanSuccess"),
    });
    scanPath.value = "";
  } catch (err) {
    scanError.value = t("browse.libraryManagement.scanError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isScanning.value = false;
  }
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="library-edit-view">
    <div v-if="loading && !library" class="library-edit-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="library-edit-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="load">{{ t("common.retry") }}</AppButton>
    </div>

    <template v-else-if="library && isOwner">
      <h1 class="library-edit-view__title">
        {{ t("browse.edit.editLibrary") }}
      </h1>

      <form class="library-edit-view__form" @submit.prevent="onSubmit">
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

        <div class="library-edit-view__actions">
          <AppButton type="submit" :loading="isSaving">
            {{ t("common.save") }}
          </AppButton>
          <AppButton
            type="button"
            variant="danger"
            :loading="isDeleting"
            @click="onDelete"
          >
            {{ t("common.delete") }}
          </AppButton>
        </div>
      </form>

      <section
        class="library-edit-view__section"
        aria-labelledby="library-upload-heading"
      >
        <h2
          id="library-upload-heading"
          class="library-edit-view__section-title"
        >
          {{ t("browse.libraryManagement.addTracks") }}
        </h2>

        <div class="library-edit-view__upload-options">
          <AppSelect
            v-model="uploadVisibility"
            :label="t('browse.detail.visibility')"
            :options="visibilityOptions"
          />
          <label class="library-edit-view__checkbox">
            <input v-model="uploadForce" type="checkbox" />
            {{ t("browse.libraryManagement.force") }}
          </label>
          <label class="library-edit-view__checkbox">
            <input v-model="uploadEnrich" type="checkbox" />
            {{ t("browse.libraryManagement.enrich") }}
          </label>
        </div>

        <div class="library-edit-view__upload-actions">
          <AppButton
            variant="secondary"
            :loading="isUploading"
            @click="singleFileInput?.click()"
          >
            {{ t("browse.libraryManagement.upload") }}
          </AppButton>
          <input
            ref="singleFileInput"
            type="file"
            accept="audio/*"
            class="library-edit-view__file-input"
            @change="onSingleFileChange"
          />

          <AppButton
            variant="secondary"
            :loading="isBulkUploading"
            @click="bulkFileInput?.click()"
          >
            {{ t("browse.libraryManagement.bulkUpload") }}
          </AppButton>
          <input
            ref="bulkFileInput"
            type="file"
            accept="audio/*"
            multiple
            class="library-edit-view__file-input"
            @change="onBulkFileChange"
          />
        </div>

        <p
          v-if="uploadError"
          class="library-edit-view__inline-error"
          role="alert"
        >
          {{ uploadError }}
        </p>
      </section>

      <section
        class="library-edit-view__section"
        aria-labelledby="library-scan-heading"
      >
        <h2 id="library-scan-heading" class="library-edit-view__section-title">
          {{ t("browse.libraryManagement.scan") }}
        </h2>

        <div class="library-edit-view__scan-row">
          <AppInput
            v-model="scanPath"
            :label="t('browse.libraryManagement.scanPath')"
            :hint="t('browse.libraryManagement.scanHint')"
          />
          <AppButton
            :loading="isScanning"
            :disabled="!scanPath.trim()"
            @click="onScan"
          >
            {{ t("browse.libraryManagement.scan") }}
          </AppButton>
        </div>

        <p
          v-if="scanError"
          class="library-edit-view__inline-error"
          role="alert"
        >
          {{ scanError }}
        </p>
      </section>

      <section
        class="library-edit-view__section"
        aria-labelledby="library-tracks-heading"
      >
        <h2
          id="library-tracks-heading"
          class="library-edit-view__section-title"
        >
          {{ t("browse.libraryManagement.libraryTracks") }}
        </h2>

        <div
          v-if="tracksError"
          class="library-edit-view__section-error"
          role="alert"
        >
          <span>{{ tracksError }}</span>
          <AppButton size="sm" @click="retryTracks">{{
            t("common.retry")
          }}</AppButton>
        </div>

        <TrackList
          :tracks="tracks"
          :loading="tracksLoading"
          :context="library.name"
          :enrich="trackEnrich"
        />

        <div class="library-edit-view__footer">
          <AppButton
            v-if="tracksHasMore"
            variant="secondary"
            :loading="tracksLoading"
            :disabled="tracksLoading"
            @click="loadMoreTracks"
          >
            {{ t("browse.list.loadMore") }}
          </AppButton>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.library-edit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 64rem;
}

.library-edit-view__skeleton {
  min-height: 16rem;
}

.library-edit-view__error,
.library-edit-view__section-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.library-edit-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.library-edit-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;
}

.library-edit-view__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}

.library-edit-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.library-edit-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.library-edit-view__upload-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-3);
  align-items: end;
}

.library-edit-view__checkbox {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text);
  font-size: 0.9375rem;
}

.library-edit-view__checkbox input {
  width: 1rem;
  height: 1rem;
}

.library-edit-view__upload-actions {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.library-edit-view__file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.library-edit-view__scan-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--space-3);
  align-items: end;
}

.library-edit-view__inline-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.library-edit-view__footer {
  display: flex;
  justify-content: center;
}
</style>
