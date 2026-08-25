<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  getTrack,
  updateTrack,
  deleteTrack,
  type TrackResponse,
  type TrackUpdate,
} from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { useOwnership } from "@/composables/useOwnership";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import type { Visibility } from "@/api/libraries";
import { parseNumber, toVisibility } from "@/utils/entity";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const toast = useToastStore();

const trackId = computed(() => String(route.params.id));
const track = ref<TrackResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const title = ref("");
const genre = ref("");
const trackNumber = ref("");
const discNumber = ref("");
const visibility = ref<Visibility>("private");
const isSaving = ref(false);
const isDeleting = ref(false);

const { isOwner } = useOwnership(computed(() => track.value?.owner_id ?? null));

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

function resetForm() {
  title.value = track.value?.title ?? "";
  genre.value = track.value?.genre ?? "";
  trackNumber.value =
    track.value?.track_number != null ? String(track.value.track_number) : "";
  discNumber.value =
    track.value?.disc_number != null ? String(track.value.disc_number) : "";
  visibility.value = toVisibility(track.value?.visibility);
  error.value = null;
}

async function loadTrack() {
  loading.value = true;
  error.value = null;
  try {
    track.value = await getTrack(trackId.value);
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

  if (!isOwner.value) {
    await router.replace(`/tracks/${trackId.value}`);
    return;
  }

  resetForm();
}

async function onSubmit() {
  if (!title.value.trim()) return;

  isSaving.value = true;
  error.value = null;

  const body: TrackUpdate = {
    title: title.value.trim(),
    genre: genre.value.trim() || null,
    track_number: parseNumber(trackNumber.value),
    disc_number: parseNumber(discNumber.value),
    visibility: visibility.value,
  };

  try {
    await updateTrack(trackId.value, body);
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

    <template v-else-if="track && isOwner">
      <AppPageTitle class="track-edit-view__title" icon="pen-to-square">
        {{ t("browse.edit.editTrack") }}
      </AppPageTitle>

      <form class="track-edit-view__form" @submit.prevent="onSubmit">
        <AppInput
          v-model="title"
          :label="t('browse.edit.title')"
          :required="true"
        />
        <AppInput v-model="genre" :label="t('browse.detail.genre')" />
        <div class="track-edit-view__row">
          <AppInput
            v-model="trackNumber"
            type="number"
            :label="t('browse.detail.trackNumber')"
          />
          <AppInput
            v-model="discNumber"
            type="number"
            :label="t('browse.detail.discNumber')"
          />
        </div>
        <AppSelect
          v-model="visibility"
          :label="t('browse.detail.visibility')"
          :options="visibilityOptions"
        />

        <div class="track-edit-view__actions">
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

.track-edit-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.track-edit-view__row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.track-edit-view__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
</style>
