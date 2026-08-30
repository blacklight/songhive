<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useConfirm } from "@/composables/useConfirm";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import {
  enrichImages,
  provisionFederationKeys,
  rehashAudio,
  syncTags,
  triggerStorageCleanup,
  type SyncTagsRequest,
  type EnrichImagesRequest,
} from "@/api/admin";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSelect from "@/components/ui/AppSelect.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const { confirm } = useConfirm();

type LoadingTask =
  | "storageCleanup"
  | "syncTags"
  | "rehashAudio"
  | "provisionFederationKeys"
  | "enrichImages"
  | null;

const loadingTask = ref<LoadingTask>(null);

const syncScope = ref<"all" | "track" | "album" | "artist" | "library">("all");
const syncTargetId = ref("");
const syncDryRun = ref(false);

const scopeOptions = computed(() => [
  { value: "all", label: t("pages.admin.tasks.syncTags.scopeAll") },
  { value: "track", label: t("pages.admin.tasks.syncTags.scopeTrack") },
  { value: "album", label: t("pages.admin.tasks.syncTags.scopeAlbum") },
  { value: "artist", label: t("pages.admin.tasks.syncTags.scopeArtist") },
  { value: "library", label: t("pages.admin.tasks.syncTags.scopeLibrary") },
]);

const rehashDryRun = ref(false);
const provisionDryRun = ref(false);

const enrichImagesScope = ref<"all" | "artist" | "album">("all");
const enrichImagesTargetId = ref("");
const enrichImagesForce = ref(false);
const enrichImagesDryRun = ref(false);

const enrichImagesScopeOptions = computed(() => [
  { value: "all", label: t("pages.admin.tasks.enrichImages.scopeAll") },
  { value: "artist", label: t("pages.admin.tasks.enrichImages.scopeArtist") },
  { value: "album", label: t("pages.admin.tasks.enrichImages.scopeAlbum") },
]);

function isEnrichImagesScopeValid(): boolean {
  if (enrichImagesScope.value === "all") return true;
  return enrichImagesTargetId.value.trim().length > 0;
}

function buildEnrichImagesBody(): EnrichImagesRequest {
  const body: EnrichImagesRequest = {
    all: enrichImagesScope.value === "all",
    force: enrichImagesForce.value,
    dry_run: enrichImagesDryRun.value,
  };
  if (enrichImagesScope.value !== "all") {
    (body as Record<string, unknown>)[`${enrichImagesScope.value}_id`] =
      enrichImagesTargetId.value.trim();
  }
  return body;
}

function isSyncScopeValid(): boolean {
  if (syncScope.value === "all") return true;
  return syncTargetId.value.trim().length > 0;
}

function buildSyncBody(): SyncTagsRequest {
  const body: SyncTagsRequest = {
    all: syncScope.value === "all",
    dry_run: syncDryRun.value,
  };
  if (syncScope.value !== "all") {
    (body as Record<string, unknown>)[`${syncScope.value}_id`] =
      syncTargetId.value.trim();
  }
  return body;
}

async function runWithLoading<T>(
  task: Exclude<LoadingTask, null>,
  fn: () => Promise<T>,
): Promise<T> {
  loadingTask.value = task;
  try {
    return await fn();
  } finally {
    loadingTask.value = null;
  }
}

function showError(messageKey: string, err: unknown) {
  toastStore.push({
    type: "error",
    message: t(messageKey, {
      message: getApiErrorMessage(err) || t("errors.unknown"),
    }),
  });
}

async function onTriggerStorageCleanup() {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.tasks.storageCleanup.description"),
    danger: true,
  });
  if (!ok) return;

  try {
    await runWithLoading("storageCleanup", async () => {
      await triggerStorageCleanup();
      toastStore.push({
        type: "success",
        message: t("pages.admin.tasks.storageCleanup.triggered"),
      });
    });
  } catch (err) {
    showError("pages.admin.tasks.storageCleanup.triggerError", err);
  }
}

async function onSyncTags() {
  if (!isSyncScopeValid()) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.tasks.syncTags.targetIdRequired"),
    });
    return;
  }

  if (syncScope.value === "all" && !syncDryRun.value) {
    const ok = await confirm({
      title: t("common.confirm"),
      message: t("pages.admin.tasks.syncTags.allWarning"),
      danger: true,
    });
    if (!ok) return;
  }

  try {
    const body = buildSyncBody();
    await runWithLoading("syncTags", async () => {
      const result = await syncTags(body);
      const message = body.dry_run
        ? t("pages.admin.tasks.syncTags.dryRunTriggered", {
            count: result.enqueued,
          })
        : t("pages.admin.tasks.syncTags.triggered", { count: result.enqueued });
      toastStore.push({ type: "success", message });
    });
  } catch (err) {
    showError("pages.admin.tasks.syncTags.triggerError", err);
  }
}

async function onRehashAudio() {
  if (!rehashDryRun.value) {
    const ok = await confirm({
      title: t("common.confirm"),
      message: t("pages.admin.tasks.rehashAudio.description"),
      danger: true,
    });
    if (!ok) return;
  }

  try {
    await runWithLoading("rehashAudio", async () => {
      await rehashAudio({ dry_run: rehashDryRun.value });
      toastStore.push({
        type: "success",
        message: t("pages.admin.tasks.rehashAudio.triggered"),
      });
    });
  } catch (err) {
    showError("pages.admin.tasks.rehashAudio.triggerError", err);
  }
}

async function onProvisionFederationKeys() {
  if (!provisionDryRun.value) {
    const ok = await confirm({
      title: t("common.confirm"),
      message: t("pages.admin.tasks.provisionFederationKeys.description"),
      danger: false,
    });
    if (!ok) return;
  }

  try {
    await runWithLoading("provisionFederationKeys", async () => {
      await provisionFederationKeys({ dry_run: provisionDryRun.value });
      toastStore.push({
        type: "success",
        message: t("pages.admin.tasks.provisionFederationKeys.triggered"),
      });
    });
  } catch (err) {
    showError("pages.admin.tasks.provisionFederationKeys.triggerError", err);
  }
}

async function onEnrichImages() {
  if (!isEnrichImagesScopeValid()) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.tasks.enrichImages.targetIdRequired"),
    });
    return;
  }

  if (enrichImagesScope.value === "all" && !enrichImagesDryRun.value) {
    const ok = await confirm({
      title: t("common.confirm"),
      message: t("pages.admin.tasks.enrichImages.allWarning"),
      danger: true,
    });
    if (!ok) return;
  }

  try {
    const body = buildEnrichImagesBody();
    await runWithLoading("enrichImages", async () => {
      const result = await enrichImages(body);
      const message = body.dry_run
        ? t("pages.admin.tasks.enrichImages.dryRunTriggered", {
            artists: result.artists,
            albums: result.albums,
          })
        : t("pages.admin.tasks.enrichImages.triggered", {
            artists: result.artists,
            albums: result.albums,
          });
      toastStore.push({ type: "success", message });
    });
  } catch (err) {
    showError("pages.admin.tasks.enrichImages.triggerError", err);
  }
}
</script>

<template>
  <div class="tasks-view">
    <AppPageTitle icon="list-check">
      {{ t("pages.admin.tasks.title") }}
    </AppPageTitle>

    <section class="tasks-view__card">
      <h2 class="tasks-view__card-title">
        <AppIcon name="broom" spacing="right" />
        {{ t("pages.admin.tasks.storageCleanup.title") }}
      </h2>
      <p class="tasks-view__description">
        {{ t("pages.admin.tasks.storageCleanup.description") }}
      </p>
      <AppButton
        :loading="loadingTask === 'storageCleanup'"
        :disabled="loadingTask !== null"
        icon="broom"
        @click="onTriggerStorageCleanup"
      >
        {{ t("pages.admin.tasks.storageCleanup.trigger") }}
      </AppButton>
    </section>

    <section class="tasks-view__card">
      <h2 class="tasks-view__card-title">
        <AppIcon name="tag" spacing="right" />
        {{ t("pages.admin.tasks.syncTags.title") }}
      </h2>
      <p class="tasks-view__description">
        {{ t("pages.admin.tasks.syncTags.description") }}
      </p>
      <div class="tasks-view__controls">
        <AppSelect
          v-model="syncScope"
          :label="t('pages.admin.tasks.syncTags.scopeLabel')"
          :options="scopeOptions"
          :disabled="loadingTask !== null"
        />
        <AppInput
          v-if="syncScope !== 'all'"
          v-model="syncTargetId"
          :label="t('pages.admin.tasks.syncTags.targetIdLabel')"
          :hint="t('pages.admin.tasks.syncTags.targetIdHint')"
          :disabled="loadingTask !== null"
        />
        <AppCheckbox
          v-model="syncDryRun"
          :label="t('pages.admin.tasks.syncTags.dryRun')"
          :disabled="loadingTask !== null"
        />
      </div>
      <AppButton
        :loading="loadingTask === 'syncTags'"
        :disabled="loadingTask !== null"
        icon="tag"
        @click="onSyncTags"
      >
        {{ t("pages.admin.tasks.syncTags.trigger") }}
      </AppButton>
    </section>

    <section class="tasks-view__card">
      <h2 class="tasks-view__card-title">
        <AppIcon name="rotate" spacing="right" />
        {{ t("pages.admin.tasks.rehashAudio.title") }}
      </h2>
      <p class="tasks-view__description">
        {{ t("pages.admin.tasks.rehashAudio.description") }}
      </p>
      <div class="tasks-view__controls">
        <AppCheckbox
          v-model="rehashDryRun"
          :label="t('pages.admin.tasks.rehashAudio.dryRun')"
          :disabled="loadingTask !== null"
        />
      </div>
      <AppButton
        :loading="loadingTask === 'rehashAudio'"
        :disabled="loadingTask !== null"
        icon="rotate"
        @click="onRehashAudio"
      >
        {{ t("pages.admin.tasks.rehashAudio.trigger") }}
      </AppButton>
    </section>

    <section class="tasks-view__card">
      <h2 class="tasks-view__card-title">
        <AppIcon name="key" spacing="right" />
        {{ t("pages.admin.tasks.provisionFederationKeys.title") }}
      </h2>
      <p class="tasks-view__description">
        {{ t("pages.admin.tasks.provisionFederationKeys.description") }}
      </p>
      <div class="tasks-view__controls">
        <AppCheckbox
          v-model="provisionDryRun"
          :label="t('pages.admin.tasks.provisionFederationKeys.dryRun')"
          :disabled="loadingTask !== null"
        />
      </div>
      <AppButton
        :loading="loadingTask === 'provisionFederationKeys'"
        :disabled="loadingTask !== null"
        icon="key"
        @click="onProvisionFederationKeys"
      >
        {{ t("pages.admin.tasks.provisionFederationKeys.trigger") }}
      </AppButton>
    </section>

    <section class="tasks-view__card">
      <h2 class="tasks-view__card-title">
        <AppIcon name="image" spacing="right" />
        {{ t("pages.admin.tasks.enrichImages.title") }}
      </h2>
      <p class="tasks-view__description">
        {{ t("pages.admin.tasks.enrichImages.description") }}
      </p>
      <div class="tasks-view__controls">
        <AppSelect
          v-model="enrichImagesScope"
          :label="t('pages.admin.tasks.enrichImages.scopeLabel')"
          :options="enrichImagesScopeOptions"
          :disabled="loadingTask !== null"
        />
        <AppInput
          v-if="enrichImagesScope !== 'all'"
          v-model="enrichImagesTargetId"
          :label="t('pages.admin.tasks.enrichImages.targetIdLabel')"
          :hint="t('pages.admin.tasks.enrichImages.targetIdHint')"
          :disabled="loadingTask !== null"
        />
        <AppCheckbox
          v-model="enrichImagesForce"
          :label="t('pages.admin.tasks.enrichImages.force')"
          :disabled="loadingTask !== null"
        />
        <AppCheckbox
          v-model="enrichImagesDryRun"
          :label="t('pages.admin.tasks.enrichImages.dryRun')"
          :disabled="loadingTask !== null"
        />
      </div>
      <AppButton
        :loading="loadingTask === 'enrichImages'"
        :disabled="loadingTask !== null"
        icon="image"
        @click="onEnrichImages"
      >
        {{ t("pages.admin.tasks.enrichImages.trigger") }}
      </AppButton>
    </section>
  </div>
</template>

<style scoped>
.tasks-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.tasks-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-6);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  max-width: 40rem;
}

.tasks-view__card-title {
  display: flex;
  align-items: center;
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
}

.tasks-view__description {
  margin: 0;
  color: var(--color-text-muted);
}

.tasks-view__controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
</style>
