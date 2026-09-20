<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  deletePodcastSyncConfig,
  getPodcastSyncConfig,
  putPodcastSyncConfig,
  syncPodcastsNow,
  type PodcastSyncConfigResponse,
  type PodcastSyncMode,
  type PodcastSyncServerType,
} from "@/api/podcasts";
import { getApiErrorMessage } from "@/api/client";
import { formatDateTime } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toast = useToastStore();

const isLoading = ref(false);
const isSaving = ref(false);
const isSyncing = ref(false);
const isDeleting = ref(false);
const loadError = ref<string | null>(null);

const existing = ref<PodcastSyncConfigResponse | null>(null);
const serverType = ref<PodcastSyncServerType>("gpodder");
const serverUrl = ref("");
const username = ref("");
const password = ref("");
const deviceId = ref("songhive");
const mode = ref<PodcastSyncMode>("pull");
const enabled = ref(true);
const lastResult = ref<string | null>(null);

const serverTypeOptions = computed(() => [
  { value: "gpodder", label: t("pages.podcasts.sync.serverTypeGpodder") },
  { value: "nextcloud", label: t("pages.podcasts.sync.serverTypeNextcloud") },
]);

const modeOptions = computed(() => [
  { value: "pull", label: t("pages.podcasts.sync.modePull") },
  { value: "bidirectional", label: t("pages.podcasts.sync.modeBidirectional") },
]);

const serverUrlHint = computed(() =>
  serverType.value === "nextcloud"
    ? t("pages.podcasts.sync.serverUrlHintNextcloud")
    : t("pages.podcasts.sync.serverUrlHint"),
);

const passwordHint = computed(() => {
  if (existing.value?.has_password) {
    return t("pages.podcasts.sync.passwordKeepHint");
  }
  return serverType.value === "nextcloud"
    ? t("pages.podcasts.sync.passwordHintNextcloud")
    : t("pages.podcasts.sync.passwordHint");
});

const lastSyncedLabel = computed(() =>
  existing.value?.last_synced_at
    ? formatDateTime(existing.value.last_synced_at)
    : t("pages.podcasts.sync.neverSynced"),
);

function applyConfig(config: PodcastSyncConfigResponse) {
  existing.value = config;
  serverType.value = config.server_type;
  serverUrl.value = config.server_url;
  username.value = config.username;
  password.value = "";
  deviceId.value = config.device_id;
  mode.value = config.mode;
  enabled.value = config.enabled;
}

async function fetchConfig() {
  isLoading.value = true;
  loadError.value = null;
  try {
    const config = await getPodcastSyncConfig();
    if (config) {
      applyConfig(config);
    }
  } catch (err) {
    loadError.value =
      getApiErrorMessage(err) ||
      t("pages.podcasts.sync.loadError", {
        message: err instanceof Error ? err.message : "",
      });
  } finally {
    isLoading.value = false;
  }
}

async function save() {
  isSaving.value = true;
  try {
    const config = await putPodcastSyncConfig({
      server_type: serverType.value,
      server_url: serverUrl.value,
      username: username.value,
      // Only send a password when the field was filled in — the backend
      // keeps the stored one otherwise.
      ...(password.value ? { password: password.value } : {}),
      device_id: deviceId.value,
      mode: mode.value,
      enabled: enabled.value,
    });
    applyConfig(config);
    toast.push({ type: "success", message: t("pages.podcasts.sync.saved") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcasts.sync.saveError", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isSaving.value = false;
  }
}

async function syncNow() {
  isSyncing.value = true;
  lastResult.value = null;
  try {
    const result = await syncPodcastsNow();
    lastResult.value = t("pages.podcasts.sync.syncResult", {
      subscribed: result.subscribed,
      unsubscribed: result.unsubscribed,
      pushed: result.pushed_adds + result.pushed_removes,
      errors: result.errors.length,
    });
    const config = await getPodcastSyncConfig();
    if (config) {
      applyConfig(config);
    }
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcasts.sync.syncError", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isSyncing.value = false;
  }
}

async function remove() {
  isDeleting.value = true;
  try {
    await deletePodcastSyncConfig();
    existing.value = null;
    password.value = "";
    toast.push({ type: "success", message: t("pages.podcasts.sync.deleted") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcasts.sync.deleteError", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isDeleting.value = false;
  }
}

onMounted(fetchConfig);
</script>

<template>
  <section class="podcast-sync">
    <h2 class="podcast-sync__title">{{ t("pages.podcasts.sync.title") }}</h2>
    <p class="podcast-sync__hint">{{ t("pages.podcasts.sync.hint") }}</p>

    <div v-if="isLoading" class="podcast-sync__loading">
      <AppSpinner />
    </div>

    <div v-else-if="loadError" class="podcast-sync__error" role="alert">
      <span>{{ loadError }}</span>
      <AppButton size="sm" icon="rotate-right" @click="fetchConfig">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <form v-else class="podcast-sync__form" @submit.prevent="save">
      <AppSelect
        v-model="serverType"
        :options="serverTypeOptions"
        :label="t('pages.podcasts.sync.serverType')"
      />
      <AppInput
        v-model="serverUrl"
        type="url"
        :label="t('pages.podcasts.sync.serverUrl')"
        :hint="serverUrlHint"
        required
      />
      <AppInput
        v-model="username"
        :label="t('pages.podcasts.sync.username')"
        required
      />
      <AppInput
        v-model="password"
        type="password"
        :label="t('pages.podcasts.sync.password')"
        :hint="passwordHint"
        :required="!existing?.has_password"
      />
      <AppInput
        v-if="serverType === 'gpodder'"
        v-model="deviceId"
        :label="t('pages.podcasts.sync.deviceId')"
        :hint="t('pages.podcasts.sync.deviceIdHint')"
      />
      <AppSelect
        v-model="mode"
        :options="modeOptions"
        :label="t('pages.podcasts.sync.mode')"
        :hint="t('pages.podcasts.sync.modeHint')"
      />
      <AppCheckbox
        v-model="enabled"
        :label="t('pages.podcasts.sync.enabled')"
        :hint="t('pages.podcasts.sync.enabledHint')"
      />

      <div class="podcast-sync__actions">
        <AppButton type="submit" :loading="isSaving">{{
          t("common.save")
        }}</AppButton>
        <AppButton
          v-if="existing"
          variant="secondary"
          icon="rotate"
          :loading="isSyncing"
          @click="syncNow"
        >
          {{ t("pages.podcasts.sync.syncNow") }}
        </AppButton>
        <AppButton
          v-if="existing"
          variant="danger"
          icon="trash"
          :loading="isDeleting"
          @click="remove"
        >
          {{ t("pages.podcasts.sync.delete") }}
        </AppButton>
      </div>

      <div v-if="existing" class="podcast-sync__status">
        <p class="podcast-sync__last-synced">
          {{ t("pages.podcasts.sync.lastSynced", { date: lastSyncedLabel }) }}
        </p>
        <p
          v-if="existing.last_error"
          class="podcast-sync__last-error"
          role="alert"
        >
          {{
            t("pages.podcasts.sync.lastError", { message: existing.last_error })
          }}
        </p>
        <p v-if="lastResult" class="podcast-sync__result">{{ lastResult }}</p>
      </div>
    </form>
  </section>
</template>

<style scoped>
.podcast-sync {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.podcast-sync__title {
  margin: 0;
  font-size: 1.25rem;
}

.podcast-sync__hint {
  margin: 0;
  color: var(--color-text-muted);
}

.podcast-sync__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 32rem;
}

.podcast-sync__loading,
.podcast-sync__error {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.podcast-sync__error {
  color: var(--color-danger);
}

.podcast-sync__actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.podcast-sync__status {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.podcast-sync__last-synced,
.podcast-sync__result {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.podcast-sync__last-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
