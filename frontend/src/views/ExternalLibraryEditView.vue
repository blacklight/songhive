<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import type { Visibility } from "@/api/libraries";
import {
  listUserProviders,
  listAdminProviders,
  createUserExternalLibrary,
  adminCreateExternalLibrary,
  getUserExternalLibrary,
  adminGetExternalLibrary,
  updateUserExternalLibrary,
  adminUpdateExternalLibrary,
  deleteUserExternalLibrary,
  adminDeleteExternalLibrary,
  syncUserExternalLibrary,
  adminSyncExternalLibrary,
  listUserExternalTracks,
  adminListExternalTracks,
  restoreUserExternalTrack,
  adminRestoreExternalTrack,
  deleteUserExternalTrack,
  adminDeleteExternalTrack,
  listUserSyncRuns,
  adminListExternalSyncRuns,
  type ExternalLibraryResponse,
  type ExternalProviderResponse,
  type ExternalLibraryCreate,
  type ExternalLibraryUpdate,
  type ExternalTrackResponse,
  type ExternalSyncRunResponse,
} from "@/api/externalLibraries";
import { getApiErrorMessage } from "@/api/client";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppTabs from "@/components/ui/AppTabs.vue";
import AppTable from "@/components/ui/AppTable.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const confirm = useConfirmStore();
const toast = useToastStore();

const isAdmin = computed(() => route.path.startsWith("/admin"));
const libraryId = computed(() => {
  const id = route.params.id as string | undefined;
  return id && id !== "new" ? id : "";
});
const isNew = computed(() => !libraryId.value);
const basePath = computed(() =>
  isAdmin.value ? "/admin/external-libraries" : "/profile/external-libraries",
);

const tab = ref<"details" | "tracks" | "syncRuns">("details");
const tabs = computed(() => [
  { value: "details", label: t("common.edit") },
  { value: "tracks", label: t("pages.externalLibraries.tracks") },
  { value: "syncRuns", label: t("pages.externalLibraries.syncRuns") },
]);

const providers = ref<ExternalProviderResponse[]>([]);
const library = ref<ExternalLibraryResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const isSaving = ref(false);
const isDeleting = ref(false);
const isSyncing = ref(false);

const providerType = ref("");
const name = ref("");
const configText = ref("{}");
const visibility = ref<Visibility>("private");
const enabled = ref(true);
const syncEnabled = ref(true);
const syncInterval = ref<number | null>(null);
const includeInLibraryIndex = ref(false);

const configError = ref<string | null>(null);

const tracks = ref<ExternalTrackResponse[]>([]);
const tracksLoading = ref(false);
const tracksError = ref<string | null>(null);
const trackState = ref<string>("");
const tracksTotal = ref(0);
const tracksPage = ref(1);
const tracksPerPage = 20;

const syncRuns = ref<ExternalSyncRunResponse[]>([]);
const syncRunsLoading = ref(false);
const syncRunsError = ref<string | null>(null);
const syncRunsTotal = ref(0);
const syncRunsPage = ref(1);
const syncRunsPerPage = 20;
const activeSyncRunId = ref<string | null>(null);
let syncPollTimeout: ReturnType<typeof setTimeout> | null = null;

const selectedTrackIds = ref<Set<string>>(new Set());

const providerOptions = computed(() =>
  providers.value.map((provider) => ({
    value: provider.provider_type,
    label: provider.provider_type,
  })),
);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

const stateOptions = computed(() => [
  { value: "", label: t("common.all") },
  { value: "active", label: "Active" },
  { value: "shadowed", label: "Shadowed" },
  { value: "tombstoned", label: "Tombstoned" },
  { value: "missing", label: "Missing" },
  { value: "error", label: "Error" },
]);

const trackColumns = computed(() => [
  { key: "selected", label: "", align: "center" as const, width: "2.5rem" },
  { key: "provider_key", label: t("pages.externalLibraries.trackProviderKey") },
  {
    key: "state",
    label: t("pages.externalLibraries.trackState"),
    align: "center" as const,
  },
  { key: "display_path", label: t("pages.externalLibraries.trackDisplayPath") },
  { key: "last_seen_at", label: t("pages.externalLibraries.lastSeen") },
  {
    key: "actions",
    label: t("browse.detail.actions"),
    align: "center" as const,
  },
]);

const trackRows = computed<Record<string, unknown>[]>(() =>
  tracks.value.map((track) => ({
    id: track.id,
    trackId: track.id,
    selected: selectedTrackIds.value.has(track.id),
    provider_key: track.provider_key,
    state: track.state,
    display_path: track.display_path || "—",
    last_seen_at: track.last_seen_at
      ? new Date(track.last_seen_at).toLocaleString()
      : "—",
  })),
);

const syncRunColumns = computed(() => [
  { key: "status", label: t("pages.externalLibraries.syncRunStatus") },
  { key: "items_seen", label: "Seen", align: "right" as const },
  { key: "tracks_created", label: "Created", align: "right" as const },
  { key: "tracks_updated", label: "Updated", align: "right" as const },
  { key: "tracks_tombstoned", label: "Tombstoned", align: "right" as const },
  { key: "tracks_shadowed", label: "Shadowed", align: "right" as const },
  { key: "started_at", label: "Started" },
]);

const syncRunRows = computed<Record<string, unknown>[]>(() =>
  syncRuns.value.map((run) => ({
    id: run.id,
    status: run.status,
    items_seen: run.items_seen,
    tracks_created: run.tracks_created,
    tracks_updated: run.tracks_updated,
    tracks_tombstoned: run.tracks_tombstoned,
    tracks_shadowed: run.tracks_shadowed,
    started_at: run.started_at
      ? new Date(run.started_at).toLocaleString()
      : "—",
  })),
);

async function loadProviders() {
  try {
    providers.value = isAdmin.value
      ? await listAdminProviders()
      : await listUserProviders();
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("errors.unknown");
  }
}

async function loadLibrary() {
  if (isNew.value) {
    resetForm();
    return;
  }
  loading.value = true;
  error.value = null;
  try {
    library.value = isAdmin.value
      ? await adminGetExternalLibrary(libraryId.value)
      : await getUserExternalLibrary(libraryId.value);
    resetForm();
  } catch (err) {
    error.value = t("pages.externalLibraries.loadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    loading.value = false;
  }
}

function resetForm() {
  const source = library.value;
  if (!source) {
    providerType.value = providers.value[0]?.provider_type ?? "";
    name.value = "";
    configText.value = "{}";
    visibility.value = "private";
    enabled.value = true;
    syncEnabled.value = true;
    syncInterval.value = null;
    includeInLibraryIndex.value = false;
    return;
  }
  providerType.value = source.provider_type;
  name.value = source.name ?? "";
  configText.value = JSON.stringify(source.config ?? {}, null, 2);
  enabled.value = source.enabled;
  syncEnabled.value = source.sync_enabled;
  syncInterval.value = source.sync_interval_seconds ?? null;
  includeInLibraryIndex.value = source.include_in_library_index;
}

function validateConfig(): Record<string, unknown> | null {
  try {
    return JSON.parse(configText.value) as Record<string, unknown>;
  } catch {
    configError.value = t("pages.externalLibraries.configError");
    return null;
  }
}

async function onSubmit() {
  configError.value = null;
  const config = validateConfig();
  if (!config) return;

  isSaving.value = true;
  error.value = null;

  try {
    if (isNew.value) {
      const body: ExternalLibraryCreate = {
        provider_type: providerType.value,
        name: name.value.trim() || null,
        config,
        visibility: visibility.value,
        enabled: enabled.value,
        sync_enabled: syncEnabled.value,
        sync_interval_seconds: syncInterval.value,
        include_in_library_index: isAdmin.value
          ? includeInLibraryIndex.value
          : false,
      };
      const created = isAdmin.value
        ? await adminCreateExternalLibrary(body)
        : await createUserExternalLibrary(body);
      toast.push({
        type: "success",
        message: t("pages.externalLibraries.createSuccess"),
      });
      void router.replace(`${basePath.value}/${created.id}`);
    } else {
      const body: ExternalLibraryUpdate = {
        name: name.value.trim() || null,
        config,
        enabled: enabled.value,
        sync_enabled: syncEnabled.value,
        sync_interval_seconds: syncInterval.value,
      };
      if (isAdmin.value) {
        body.include_in_library_index = includeInLibraryIndex.value;
      }
      const updated = isAdmin.value
        ? await adminUpdateExternalLibrary(libraryId.value, body)
        : await updateUserExternalLibrary(libraryId.value, body);
      library.value = updated;
      toast.push({
        type: "success",
        message: t("pages.externalLibraries.saveSuccess"),
      });
      void router.replace(`${basePath.value}/${updated.id}`);
    }
  } catch (err) {
    error.value = t("pages.externalLibraries.saveError", {
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
    message: t("pages.externalLibraries.deleteConfirm", {
      name: library.value.name || library.value.provider_type,
    }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  isDeleting.value = true;
  try {
    if (isAdmin.value) {
      await adminDeleteExternalLibrary(libraryId.value);
    } else {
      await deleteUserExternalLibrary(libraryId.value);
    }
    toast.push({
      type: "success",
      message: t("pages.externalLibraries.deleteSuccess"),
    });
    void router.push(basePath.value);
  } catch (err) {
    error.value = t("pages.externalLibraries.deleteError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    isDeleting.value = false;
  }
}

function stopSyncPolling() {
  if (syncPollTimeout !== null) {
    clearTimeout(syncPollTimeout);
    syncPollTimeout = null;
  }
  activeSyncRunId.value = null;
}

async function onSync() {
  stopSyncPolling();
  isSyncing.value = true;
  try {
    const result = isAdmin.value
      ? await adminSyncExternalLibrary(libraryId.value)
      : await syncUserExternalLibrary(libraryId.value);
    activeSyncRunId.value = result.sync_run_id;
    tab.value = "syncRuns";
    await loadSyncRuns();
    void pollSyncRunStatus();
  } catch (err) {
    isSyncing.value = false;
    error.value = t("pages.externalLibraries.syncError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  }
}

async function pollSyncRunStatus(attempts = 0) {
  const runId = activeSyncRunId.value;
  if (runId === null || !libraryId.value) {
    isSyncing.value = false;
    return;
  }

  if (attempts > 0) {
    await loadSyncRuns();
  }

  const run = syncRuns.value.find((r) => r.id === runId);
  if (run && ["success", "partial", "failed"].includes(run.status)) {
    stopSyncPolling();
    isSyncing.value = false;
    await loadLibrary();
    if (run.status === "success") {
      toast.push({
        type: "success",
        message: t("pages.externalLibraries.syncSuccess"),
      });
    } else if (run.status === "partial") {
      toast.push({
        type: "warning",
        message: t("pages.externalLibraries.syncPartial"),
      });
    } else {
      toast.push({
        type: "error",
        message: t("pages.externalLibraries.syncFailed"),
      });
    }
    return;
  }

  if (attempts >= 30) {
    stopSyncPolling();
    isSyncing.value = false;
    error.value = t("pages.externalLibraries.syncTimeout");
    return;
  }

  syncPollTimeout = setTimeout(() => {
    void pollSyncRunStatus(attempts + 1);
  }, 2000);
}

async function loadTracks() {
  if (isNew.value) return;
  tracksLoading.value = true;
  tracksError.value = null;
  try {
    const offset = (tracksPage.value - 1) * tracksPerPage;
    const result = isAdmin.value
      ? await adminListExternalTracks(libraryId.value, {
          state: trackState.value || undefined,
          limit: tracksPerPage,
          offset,
        })
      : await listUserExternalTracks(libraryId.value, {
          state: trackState.value || undefined,
          limit: tracksPerPage,
          offset,
        });
    tracks.value = result.tracks;
    tracksTotal.value = result.total;
  } catch (err) {
    tracksError.value = t("pages.externalLibraries.loadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    tracksLoading.value = false;
  }
}

async function loadSyncRuns() {
  if (isNew.value) return;
  syncRunsLoading.value = true;
  syncRunsError.value = null;
  try {
    const offset = (syncRunsPage.value - 1) * syncRunsPerPage;
    const result = isAdmin.value
      ? await adminListExternalSyncRuns(libraryId.value, {
          limit: syncRunsPerPage,
          offset,
        })
      : await listUserSyncRuns(libraryId.value, {
          limit: syncRunsPerPage,
          offset,
        });
    syncRuns.value = result.syncRuns;
    syncRunsTotal.value = result.total;
  } catch (err) {
    syncRunsError.value = t("pages.externalLibraries.loadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    syncRunsLoading.value = false;
  }
}

async function onRestoreTrack(trackId: string) {
  try {
    const restored = isAdmin.value
      ? await adminRestoreExternalTrack(libraryId.value, trackId)
      : await restoreUserExternalTrack(libraryId.value, trackId);
    const index = tracks.value.findIndex((t) => t.id === trackId);
    if (index !== -1) tracks.value[index] = restored;
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.externalLibraries.saveError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  }
}

async function onDeleteTrack(trackId: string) {
  const track = tracks.value.find((t) => t.id === trackId);
  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.delete.confirm", {
      name: track?.provider_key ?? trackId,
    }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  try {
    const body = { delete_source: false, remove_songhive_track: false };
    if (isAdmin.value) {
      await adminDeleteExternalTrack(libraryId.value, trackId, body);
    } else {
      await deleteUserExternalTrack(libraryId.value, trackId, body);
    }
    await loadTracks();
  } catch (err) {
    toast.push({
      type: "error",
      message: t("browse.delete.error", {
        entity: t("pages.externalLibraries.tracks"),
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  }
}

async function onBulkDelete() {
  const ids = Array.from(selectedTrackIds.value);
  if (ids.length === 0) return;
  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("browse.delete.bulkConfirm", {
      count: ids.length,
      entity: t("pages.externalLibraries.tracks"),
    }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  try {
    const body = { delete_source: false, remove_songhive_track: false };
    if (isAdmin.value) {
      // Admin bulk delete endpoint; import dynamically to keep the client thin.
      const { adminBulkDeleteExternalTracks } =
        await import("@/api/externalLibraries");
      await adminBulkDeleteExternalTracks(libraryId.value, {
        external_track_ids: ids,
        ...body,
      });
    } else {
      for (const id of ids) {
        await deleteUserExternalTrack(libraryId.value, id, body);
      }
    }
    selectedTrackIds.value.clear();
    await loadTracks();
  } catch (err) {
    toast.push({
      type: "error",
      message: t("browse.delete.error", {
        entity: t("pages.externalLibraries.tracks"),
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  }
}

function toggleSelectAll() {
  const allSelected =
    selectedTrackIds.value.size === tracks.value.length &&
    tracks.value.length > 0;
  if (allSelected) {
    selectedTrackIds.value.clear();
  } else {
    tracks.value.forEach((track) => selectedTrackIds.value.add(track.id));
  }
}

function toggleTrack(trackId: string) {
  if (selectedTrackIds.value.has(trackId)) {
    selectedTrackIds.value.delete(trackId);
  } else {
    selectedTrackIds.value.add(trackId);
  }
}

watch([tab, trackState, tracksPage], () => {
  if (tab.value === "tracks") void loadTracks();
});
watch([tab, syncRunsPage], () => {
  if (tab.value === "syncRuns") void loadSyncRuns();
});
watch(
  () => route.params.id,
  () => {
    void loadLibrary();
  },
);

onMounted(() => {
  void loadProviders().then(() => {
    void loadLibrary();
  });
});

onUnmounted(() => {
  stopSyncPolling();
});
</script>

<template>
  <div class="external-library-edit-view">
    <div
      v-if="loading && !library && !isNew"
      class="external-library-edit-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div
      v-else-if="error"
      class="external-library-edit-view__error"
      role="alert"
    >
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="loadLibrary">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else>
      <div class="external-library-edit-view__header">
        <AppPageTitle icon="cloud">
          {{
            isNew
              ? t("pages.externalLibraries.newTitle")
              : t("pages.externalLibraries.editTitle")
          }}
        </AppPageTitle>
        <div class="external-library-edit-view__header-actions">
          <AppButton
            v-if="!isNew"
            variant="secondary"
            :loading="isSyncing"
            icon="rotate"
            @click="onSync"
          >
            {{ t("pages.externalLibraries.sync") }}
          </AppButton>
          <AppButton
            v-if="!isNew"
            variant="danger"
            :loading="isDeleting"
            icon="trash"
            @click="onDelete"
          >
            {{ t("common.delete") }}
          </AppButton>
        </div>
      </div>

      <AppTabs v-if="!isNew" v-model="tab" :tabs="tabs" />

      <template v-if="tab === 'details'">
        <form
          class="external-library-edit-view__form"
          @submit.prevent="onSubmit"
        >
          <AppSelect
            v-model="providerType"
            :label="t('pages.externalLibraries.provider')"
            :options="providerOptions"
            :disabled="!isNew"
          />
          <AppInput v-model="name" :label="t('pages.externalLibraries.name')" />
          <AppInput
            v-model="configText"
            as="textarea"
            :label="t('pages.externalLibraries.config')"
            :hint="t('pages.externalLibraries.configHint')"
            :error="configError ?? undefined"
          />
          <AppSelect
            v-model="visibility"
            :label="t('pages.externalLibraries.visibility')"
            :options="visibilityOptions"
          />
          <AppCheckbox
            v-model="enabled"
            :label="t('pages.externalLibraries.enabled')"
          />
          <AppCheckbox
            v-model="syncEnabled"
            :label="t('pages.externalLibraries.syncEnabled')"
          />
          <AppInput
            :model-value="syncInterval ?? ''"
            type="number"
            :label="t('pages.externalLibraries.syncInterval')"
            @update:model-value="
              syncInterval = $event === '' ? null : Number($event)
            "
          />
          <AppCheckbox
            v-if="isAdmin"
            v-model="includeInLibraryIndex"
            :label="t('pages.externalLibraries.includeInLibraryIndex')"
          />

          <div class="external-library-edit-view__actions">
            <AppButton type="submit" :loading="isSaving" icon="floppy-disk">
              {{
                isNew
                  ? t("pages.externalLibraries.create")
                  : t("pages.externalLibraries.save")
              }}
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              @click="() => router.push(basePath)"
            >
              {{ t("common.cancel") }}
            </AppButton>
          </div>
        </form>
      </template>

      <template v-else-if="tab === 'tracks'">
        <div class="external-library-edit-view__section">
          <div class="external-library-edit-view__section-controls">
            <AppSelect v-model="trackState" :options="stateOptions" />
            <AppButton
              v-if="selectedTrackIds.size"
              variant="danger"
              icon="trash"
              @click="onBulkDelete"
            >
              {{ t("pages.externalLibraries.bulkDelete") }}
            </AppButton>
          </div>

          <div
            v-if="tracksError"
            class="external-library-edit-view__section-error"
            role="alert"
          >
            <span>{{ tracksError }}</span>
            <AppButton size="sm" icon="rotate-right" @click="loadTracks">
              {{ t("common.retry") }}
            </AppButton>
          </div>

          <AppTable
            :columns="trackColumns"
            :rows="trackRows"
            :row-key="(row) => String(row.id)"
            :loading="tracksLoading"
            :empty-label="
              t('browse.list.empty', {
                entity: t('pages.externalLibraries.tracks'),
              })
            "
          >
            <template #column-selected>
              <AppCheckbox
                :model-value="
                  selectedTrackIds.size === tracks.length && tracks.length > 0
                "
                :aria-label="t('pages.externalLibraries.selectAll')"
                @update:model-value="toggleSelectAll"
              />
            </template>

            <template #row-selected="{ row }">
              <AppCheckbox
                :model-value="Boolean(row.selected)"
                :aria-label="t('pages.externalLibraries.selectAll')"
                @update:model-value="toggleTrack(String(row.trackId))"
              />
            </template>

            <template #row-state="{ value }">
              <span class="external-library-edit-view__state">{{ value }}</span>
            </template>

            <template #row-actions="{ row }">
              <div class="external-library-edit-view__row-actions">
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon="rotate-right"
                  :aria-label="t('pages.externalLibraries.restore')"
                  @click="onRestoreTrack(String(row.trackId))"
                />
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon="trash"
                  :aria-label="t('pages.externalLibraries.deleteTrack')"
                  @click="onDeleteTrack(String(row.trackId))"
                />
              </div>
            </template>
          </AppTable>

          <AppPagination
            v-if="tracksTotal > tracksPerPage"
            v-model:page="tracksPage"
            :total="tracksTotal"
            :per-page="tracksPerPage"
          />
        </div>
      </template>

      <template v-else-if="tab === 'syncRuns'">
        <div class="external-library-edit-view__section">
          <div
            v-if="syncRunsError"
            class="external-library-edit-view__section-error"
            role="alert"
          >
            <span>{{ syncRunsError }}</span>
            <AppButton size="sm" icon="rotate-right" @click="loadSyncRuns">
              {{ t("common.retry") }}
            </AppButton>
          </div>

          <AppTable
            :columns="syncRunColumns"
            :rows="syncRunRows"
            :row-key="(row) => String(row.id)"
            :loading="syncRunsLoading"
            :empty-label="
              t('browse.list.empty', {
                entity: t('pages.externalLibraries.syncRuns'),
              })
            "
          />

          <AppPagination
            v-if="syncRunsTotal > syncRunsPerPage"
            v-model:page="syncRunsPage"
            :total="syncRunsTotal"
            :per-page="syncRunsPerPage"
          />
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.external-library-edit-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 64rem;
}

.external-library-edit-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.external-library-edit-view__header-actions {
  display: flex;
  gap: var(--space-2);
}

.external-library-edit-view__skeleton {
  min-height: 16rem;
}

.external-library-edit-view__error,
.external-library-edit-view__section-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.external-library-edit-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 48rem;
}

.external-library-edit-view__actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.external-library-edit-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.external-library-edit-view__section-controls {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
  align-items: center;
}

.external-library-edit-view__row-actions {
  display: flex;
  gap: var(--space-1);
}

.external-library-edit-view__state {
  text-transform: capitalize;
}
</style>
