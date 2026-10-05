<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  getTrack,
  updateTrack,
  deleteTrack,
  uploadTrackImage,
  deleteTrackImage,
  type TrackResponse,
  type TrackUpdate,
} from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useCanManage } from "@/composables/useCanManage";
import { useEntityTags } from "@/composables/useEntityTags";
import { useEntityGenres } from "@/composables/useEntityGenres";
import { useConfirmStore } from "@/stores/confirm";
import { useInstanceStore } from "@/stores/instance";
import { useToastStore } from "@/stores/toast";
import { parseNumber, toVisibility } from "@/utils/entity";
import { isFieldEditable, isProviderManaged } from "@/utils/editableFields";
import { providerDisplayName } from "@/utils/providerName";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import ImageUploadField from "@/components/ui/ImageUploadField.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TrackMetadataForm from "@/components/library/TrackMetadataForm.vue";
import ProviderIcon from "@/components/external-libraries/ProviderIcon.vue";
import SaleEditor from "@/components/payments/SaleEditor.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const instanceStore = useInstanceStore();
const toast = useToastStore();

const trackId = computed(() => String(route.params.id));
const paymentsEnabled = computed(() => instanceStore.paymentsEnabled);
const track = ref<TrackResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const title = ref("");
const artistName = ref("");
const albumTitle = ref("");
const trackNumber = ref("");
const discNumber = ref("");
const releaseYear = ref("");
const filename = ref("");
const description = ref("");
const extraArtists = ref("");
const visibility = ref("private");
const publish = ref(false);
const isSaving = ref(false);
const isDeleting = ref(false);
const isUploadingImage = ref(false);
const isRemovingImage = ref(false);
const imageError = ref<string | null>(null);

const { canManage } = useCanManage(
  computed(() => track.value?.owner_id ?? null),
);

const canRenameFile = computed(
  () =>
    (!track.value?.is_external || track.value?.can_rename_source !== false) &&
    isFieldEditable(track.value?.editable_fields, "filename"),
);

const editableFields = computed(() => track.value?.editable_fields ?? null);
const providerManaged = computed(() => isProviderManaged(editableFields.value));
const managedByLabel = computed(() =>
  providerDisplayName(track.value?.external_provider_type),
);
/** Capability names the provider locks (empty when unrestricted). */
const TRACK_CAPABILITIES = [
  "title",
  "artist",
  "album",
  "artists",
  "genres",
  "track_number",
  "disc_number",
  "release_year",
  "filename",
  "description",
  "tags",
  "image",
];
const lockedFields = computed(() =>
  TRACK_CAPABILITIES.filter(
    (cap) => !isFieldEditable(editableFields.value, cap),
  ),
);
const fieldEditable = (cap: string) =>
  isFieldEditable(editableFields.value, cap);

const { tags, resetTags, syncTags } = useEntityTags();
const { genres, resetGenres, syncGenres } = useEntityGenres();

function resetForm() {
  title.value = track.value?.title ?? "";
  artistName.value = track.value?.artist?.name ?? "";
  albumTitle.value = track.value?.album?.title ?? "";
  trackNumber.value =
    track.value?.track_number != null ? String(track.value.track_number) : "";
  discNumber.value =
    track.value?.disc_number != null ? String(track.value.disc_number) : "";
  releaseYear.value =
    track.value?.release_year != null ? String(track.value.release_year) : "";
  filename.value = track.value?.filename ?? "";
  description.value = track.value?.description ?? "";
  extraArtists.value = (track.value?.extra_artists ?? []).join(", ");
  visibility.value = toVisibility(track.value?.visibility);
  publish.value = false;
  resetTags(track.value?.tags ?? null);
  resetGenres(track.value?.genres ?? null);
  error.value = null;
}

async function loadTrack() {
  loading.value = true;
  error.value = null;
  try {
    track.value = await getTrack(trackId.value, {
      include: "artist,album,tags,genres",
    });
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

async function load() {
  track.value = null;
  await loadTrack();
  if (!track.value) return;

  if (!canManage.value) {
    await router.replace(`/tracks/${trackId.value}`);
    return;
  }

  resetForm();
}

async function onSubmit() {
  if (
    (fieldEditable("title") && !title.value.trim()) ||
    (fieldEditable("artist") && !artistName.value.trim())
  ) {
    return;
  }

  isSaving.value = true;
  error.value = null;

  // Provider-managed fields are sent only when editable — the backend
  // rejects locked fields (422) merely for being present in the payload.
  const body: TrackUpdate = {
    visibility: toVisibility(visibility.value),
  };
  if (fieldEditable("title")) body.title = title.value.trim();
  if (fieldEditable("artist")) body.artist_name = artistName.value.trim();
  if (fieldEditable("album"))
    body.album_title = albumTitle.value.trim() || null;
  if (fieldEditable("genres")) body.genre = genres.value.join("; ") || null;
  if (fieldEditable("track_number"))
    body.track_number = parseNumber(trackNumber.value);
  if (fieldEditable("disc_number"))
    body.disc_number = parseNumber(discNumber.value);
  if (fieldEditable("release_year"))
    body.release_year = parseNumber(releaseYear.value);
  if (fieldEditable("description"))
    body.description = description.value.trim() || null;
  if (fieldEditable("artists")) {
    body.extra_artists = extraArtists.value
      .split(",")
      .map((name) => name.trim())
      .filter((name) => name.length > 0);
  }
  if (fieldEditable("publish")) {
    body.publish =
      publish.value &&
      instanceStore.federationEnabled &&
      visibility.value === "public";
  }

  if (canRenameFile.value) {
    body.filename = filename.value.trim() || track.value?.filename || null;
  }

  try {
    await updateTrack(trackId.value, body);
    if (fieldEditable("tags")) await syncTags("tracks", trackId.value);
    if (fieldEditable("genres")) await syncGenres("tracks", trackId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await router.push(`/tracks/${trackId.value}`);
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
  if (!track.value) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.edit.deleteConfirm", { name: track.value.title }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    await deleteTrack(trackId.value);
    toast.push({ type: "success", message: t("browse.edit.deleted") });
    await router.push("/tracks");
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

async function refreshTrack() {
  try {
    track.value = await getTrack(trackId.value, { include: "tags,genres" });
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  }
}

async function onUploadImage(file: File) {
  if (!track.value) return;
  imageError.value = null;
  isUploadingImage.value = true;
  try {
    await uploadTrackImage(trackId.value, file);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshTrack();
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
  if (!track.value) return;
  imageError.value = null;
  isRemovingImage.value = true;
  try {
    await deleteTrackImage(trackId.value);
    toast.push({ type: "success", message: t("browse.edit.saveSuccess") });
    await refreshTrack();
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

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="track-edit-view">
    <div v-if="loading && !track" class="track-edit-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="track-edit-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else-if="track && canManage">
      <AppPageTitle class="track-edit-view__title" icon="pen-to-square">
        {{ t("browse.edit.editTrack") }}
      </AppPageTitle>

      <p
        v-if="providerManaged"
        class="track-edit-view__provider-note"
        role="note"
      >
        <ProviderIcon :provider="track.external_provider_type" />
        {{ t("browse.edit.providerManaged", { provider: managedByLabel }) }}
      </p>

      <TrackMetadataForm
        v-model:title="title"
        v-model:artist-name="artistName"
        v-model:album-title="albumTitle"
        v-model:genres="genres"
        v-model:track-number="trackNumber"
        v-model:disc-number="discNumber"
        v-model:release-year="releaseYear"
        v-model:filename="filename"
        v-model:visibility="visibility"
        v-model:tags="tags"
        v-model:description="description"
        v-model:extra-artists="extraArtists"
        v-model:publish="publish"
        :previous-visibility="track.visibility"
        :can-rename-file="canRenameFile"
        :disabled-fields="lockedFields"
        @submit="onSubmit"
      >
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
      </TrackMetadataForm>

      <SaleEditor
        v-if="paymentsEnabled"
        entity-type="track"
        :entity-id="trackId"
        :duration-seconds="track.duration ?? null"
      />

      <section
        class="track-edit-view__section"
        aria-labelledby="track-image-heading"
      >
        <AppPageTitle
          id="track-image-heading"
          :level="2"
          class="track-edit-view__section-title"
          icon="image"
        >
          {{ "Track image" }}
        </AppPageTitle>

        <ImageUploadField
          :label="'Track image'"
          :image-url="track.image_url"
          :upload-label="'Upload image'"
          :remove-label="'Remove image'"
          accept="image/*"
          :loading="isUploadingImage"
          :removing="isRemovingImage"
          :disabled="!fieldEditable('image')"
          :error="imageError ?? undefined"
          @upload="onUploadImage"
          @remove="onRemoveImage"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.track-edit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 48rem;
}

.track-edit-view__skeleton {
  min-height: 16rem;
}

.track-edit-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.track-edit-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.track-edit-view__provider-note {
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

.track-edit-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.track-edit-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}
</style>
