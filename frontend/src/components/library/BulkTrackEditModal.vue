<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AppModal from "@/components/feedback/AppModal.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import TrackMetadataForm from "@/components/library/TrackMetadataForm.vue";
import {
  getTrack,
  updateTrack,
  type TrackResponse,
  type TrackUpdate,
} from "@/api/tracks";
import { addHashtags, removeHashtag } from "@/api/hashtags";
import { getApiErrorMessage } from "@/api/client";
import { parseNumber } from "@/utils/entity";
import { useToastStore } from "@/stores/toast";
import type { Visibility } from "@/api/libraries";

export interface Props {
  open: boolean;
  trackIds: string[];
}

const props = defineProps<Props>();
const emit = defineEmits<{ close: []; saved: [] }>();

const { t } = useI18n();
const toast = useToastStore();

const loading = ref(false);
const loaded = ref(false);
const saving = ref(false);
const error = ref<string | null>(null);
const fetchedTracks = ref<TrackResponse[]>([]);

const title = ref("");
const artistName = ref("");
const albumTitle = ref("");
const genres = ref<string[]>([]);
const trackNumber = ref("");
const discNumber = ref("");
const releaseYear = ref("");
const filename = ref("");
const visibility = ref("");
const hashtags = ref<string[]>([]);

interface BulkInitialValues {
  title: string;
  artistName: string;
  albumTitle: string;
  genres: string[];
  trackNumber: string;
  discNumber: string;
  releaseYear: string;
  filename: string;
  visibility: string;
  hashtags: string[];
}

const initial = ref<BulkInitialValues | null>(null);

const canRenameFile = computed(() =>
  fetchedTracks.value.every(
    (track) => !track.is_external || track.can_rename_source !== false,
  ),
);

function sharedScalar<T>(values: T[]): T | undefined {
  if (values.length === 0) return undefined;
  const first = values[0];
  return values.every((value) => value === first) ? first : undefined;
}

function sharedList(
  lists: ReadonlyArray<readonly string[] | null | undefined>,
): string[] | undefined {
  const normalized = lists.map((list) => [...(list ?? [])].sort().join(" "));
  const first = normalized[0];
  if (first === undefined || !normalized.every((v) => v === first)) {
    return undefined;
  }
  return [...(lists[0] ?? [])];
}

function numberToString(value: number | null | undefined): string {
  return value == null ? "" : String(value);
}

function listsEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value) => b.includes(value));
}

function initForm() {
  const tracks = fetchedTracks.value;

  const sharedArtistId = sharedScalar(tracks.map((track) => track.artist_id));
  const sharedAlbumId = sharedScalar(
    tracks.map((track) => track.album_id ?? null),
  );

  initial.value = {
    title: sharedScalar(tracks.map((track) => track.title)) ?? "",
    artistName:
      sharedArtistId !== undefined ? (tracks[0]?.artist?.name ?? "") : "",
    albumTitle:
      sharedAlbumId !== undefined ? (tracks[0]?.album?.title ?? "") : "",
    genres: sharedList(tracks.map((track) => track.genres)) ?? [],
    trackNumber: numberToString(
      sharedScalar(tracks.map((track) => track.track_number ?? null)),
    ),
    discNumber: numberToString(
      sharedScalar(tracks.map((track) => track.disc_number ?? null)),
    ),
    releaseYear: numberToString(
      sharedScalar(tracks.map((track) => track.release_year ?? null)),
    ),
    filename: sharedScalar(tracks.map((track) => track.filename ?? "")) ?? "",
    visibility: sharedScalar(tracks.map((track) => track.visibility)) ?? "",
    hashtags: sharedList(tracks.map((track) => track.hashtags)) ?? [],
  };

  title.value = initial.value.title;
  artistName.value = initial.value.artistName;
  albumTitle.value = initial.value.albumTitle;
  genres.value = [...initial.value.genres];
  trackNumber.value = initial.value.trackNumber;
  discNumber.value = initial.value.discNumber;
  releaseYear.value = initial.value.releaseYear;
  filename.value = initial.value.filename;
  visibility.value = initial.value.visibility;
  hashtags.value = [...initial.value.hashtags];
}

async function loadTracks() {
  if (props.trackIds.length === 0) return;
  loading.value = true;
  loaded.value = false;
  error.value = null;
  try {
    fetchedTracks.value = await Promise.all(
      props.trackIds.map((id) =>
        getTrack(id, { include: "artist,album,hashtags,genres" }),
      ),
    );
    initForm();
    loaded.value = true;
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
      void loadTracks();
    } else {
      fetchedTracks.value = [];
      initial.value = null;
      loaded.value = false;
      error.value = null;
    }
  },
);

function buildUpdate(): TrackUpdate | null {
  const init = initial.value;
  if (!init) return null;

  const body: TrackUpdate = {};

  if (title.value !== init.title && title.value.trim()) {
    body.title = title.value.trim();
  }
  if (artistName.value !== init.artistName && artistName.value.trim()) {
    body.artist_name = artistName.value.trim();
  }
  if (albumTitle.value !== init.albumTitle) {
    body.album_title = albumTitle.value.trim();
  }
  if (!listsEqual(genres.value, init.genres)) {
    body.genre = genres.value.join("; ");
  }
  if (trackNumber.value.trim() !== init.trackNumber) {
    const parsed = parseNumber(trackNumber.value);
    if (parsed != null) body.track_number = parsed;
  }
  if (discNumber.value.trim() !== init.discNumber) {
    const parsed = parseNumber(discNumber.value);
    if (parsed != null) body.disc_number = parsed;
  }
  if (releaseYear.value.trim() !== init.releaseYear) {
    const parsed = parseNumber(releaseYear.value);
    if (parsed != null) body.release_year = parsed;
  }
  if (
    canRenameFile.value &&
    filename.value !== init.filename &&
    filename.value.trim()
  ) {
    body.filename = filename.value.trim();
  }
  if (visibility.value !== init.visibility && visibility.value) {
    body.visibility = visibility.value as Visibility;
  }

  return Object.keys(body).length > 0 ? body : null;
}

async function syncTrackHashtags(track: TrackResponse, desired: string[]) {
  const current = new Set(track.hashtags ?? []);
  const toAdd = desired.filter((hashtag) => !current.has(hashtag));
  const toRemove = [...current].filter((hashtag) => !desired.includes(hashtag));
  if (toAdd.length > 0) {
    await addHashtags("tracks", track.id, { hashtags: toAdd });
  }
  for (const hashtag of toRemove) {
    await removeHashtag("tracks", track.id, hashtag);
  }
}

async function onSubmit() {
  if (loading.value || saving.value || !loaded.value) return;

  const body = buildUpdate();
  const hashtagsDirty =
    initial.value !== null &&
    !listsEqual(hashtags.value, initial.value.hashtags);

  if (!body && !hashtagsDirty) {
    error.value = t("browse.bulkEdit.nothingToUpdate");
    return;
  }

  saving.value = true;
  error.value = null;
  const desiredHashtags = [...hashtags.value];
  const results = await Promise.allSettled(
    fetchedTracks.value.map(async (track) => {
      if (body) {
        await updateTrack(track.id, body);
      }
      if (hashtagsDirty) {
        await syncTrackHashtags(track, desiredHashtags);
      }
    }),
  );
  saving.value = false;

  const failed = results.filter(
    (result): result is PromiseRejectedResult => result.status === "rejected",
  );
  const updated = results.length - failed.length;

  if (failed.length === 0) {
    toast.push({
      type: "success",
      message: t("browse.bulkEdit.saveSuccess", { count: updated }),
    });
    emit("saved");
    emit("close");
    return;
  }

  if (updated > 0) {
    toast.push({
      type: "warning",
      message: t("browse.bulkEdit.savePartial", {
        updated,
        total: results.length,
      }),
    });
    emit("saved");
  }

  const reason = failed[0]?.reason;
  error.value = t("browse.bulkEdit.saveError", {
    count: failed.length,
    message: getApiErrorMessage(reason) || t("errors.unknown"),
  });
}
</script>

<template>
  <AppModal
    :open="props.open"
    :title="
      t('browse.bulkEdit.editMetadataTitle', { count: props.trackIds.length })
    "
    :closable="!saving"
    @close="emit('close')"
  >
    <div class="bulk-track-edit">
      <p class="bulk-track-edit__hint">{{ t("browse.bulkEdit.editHint") }}</p>

      <div v-if="loading" class="bulk-track-edit__loading">
        <AppSpinner />
      </div>

      <div v-else-if="!loaded" class="bulk-track-edit__error" role="alert">
        <span>{{ error }}</span>
        <AppButton size="sm" icon="rotate-right" @click="loadTracks">
          {{ t("common.retry") }}
        </AppButton>
      </div>

      <template v-else>
        <p v-if="error" class="bulk-track-edit__error" role="alert">
          {{ error }}
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
          v-model:hashtags="hashtags"
          bulk
          :can-rename-file="canRenameFile"
          :disabled="saving"
          @submit="onSubmit"
        />
      </template>
    </div>

    <template #actions>
      <AppButton
        variant="secondary"
        icon="xmark"
        :disabled="saving"
        @click="emit('close')"
      >
        {{ t("common.cancel") }}
      </AppButton>
      <AppButton
        icon="floppy-disk"
        :loading="saving"
        :disabled="!loaded || saving"
        @click="onSubmit"
      >
        {{ t("common.save") }}
      </AppButton>
    </template>
  </AppModal>
</template>

<style scoped>
.bulk-track-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.bulk-track-edit__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.bulk-track-edit__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6) 0;
}

.bulk-track-edit__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin: 0;
  color: var(--color-danger);
}
</style>
