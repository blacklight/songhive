<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  connectScrobbler,
  disconnectScrobbler,
  getScrobbleStatus,
  updateScrobbleSettings,
  type ScrobbleStatus,
} from "@/api/scrobbling";
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
const isConnecting = ref(false);
const isSaving = ref(false);
const isDeleting = ref(false);
const loadError = ref<string | null>(null);

const status = ref<ScrobbleStatus | null>(null);
const service = ref<"lastfm" | "librefm">("lastfm");
const username = ref("");
const password = ref("");
const enabled = ref(true);
const minSeconds = ref(30);
const minPercent = ref(25);

const config = computed(() => status.value?.config ?? null);

const serviceOptions = computed(() =>
  (status.value?.services ?? []).map((s) => ({
    value: s.id,
    label: s.available
      ? s.name
      : t("pages.scrobbling.serviceUnavailable", { name: s.name }),
  })),
);

const lastScrobbledLabel = computed(() =>
  config.value?.last_scrobbled_at
    ? formatDateTime(config.value.last_scrobbled_at)
    : t("pages.scrobbling.neverScrobbled"),
);

const serviceName = computed(
  () =>
    status.value?.services.find((s) => s.id === config.value?.service)?.name ??
    config.value?.service ??
    "",
);

async function fetchStatus() {
  isLoading.value = true;
  loadError.value = null;
  try {
    status.value = await getScrobbleStatus();
    if (config.value) {
      service.value = config.value.service as "lastfm" | "librefm";
      username.value = config.value.username;
      enabled.value = config.value.enabled;
      minSeconds.value = config.value.min_seconds;
      minPercent.value = config.value.min_percent;
    }
  } catch (err) {
    loadError.value =
      getApiErrorMessage(err) ||
      t("pages.scrobbling.loadError", {
        message: err instanceof Error ? err.message : "",
      });
  } finally {
    isLoading.value = false;
  }
}

async function connect() {
  isConnecting.value = true;
  try {
    await connectScrobbler({
      service: service.value,
      username: username.value,
      password: password.value,
    });
    password.value = "";
    await fetchStatus();
    toast.push({ type: "success", message: t("pages.scrobbling.connected") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.scrobbling.connectError", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isConnecting.value = false;
  }
}

async function save() {
  isSaving.value = true;
  try {
    await updateScrobbleSettings({
      enabled: enabled.value,
      min_seconds: Number(minSeconds.value),
      min_percent: Number(minPercent.value),
    });
    await fetchStatus();
    toast.push({ type: "success", message: t("pages.scrobbling.saved") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.scrobbling.saveError", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isSaving.value = false;
  }
}

async function remove() {
  isDeleting.value = true;
  try {
    await disconnectScrobbler();
    status.value = status.value
      ? { ...status.value, config: null }
      : status.value;
    username.value = "";
    password.value = "";
    toast.push({ type: "success", message: t("pages.scrobbling.deleted") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.scrobbling.deleteError", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isDeleting.value = false;
  }
}

onMounted(fetchStatus);
</script>

<template>
  <section class="scrobbling">
    <h2 class="scrobbling__title">{{ t("pages.scrobbling.title") }}</h2>
    <p class="scrobbling__hint">{{ t("pages.scrobbling.hint") }}</p>

    <div v-if="isLoading" class="scrobbling__loading">
      <AppSpinner />
    </div>

    <div v-else-if="loadError" class="scrobbling__error" role="alert">
      <span>{{ loadError }}</span>
      <AppButton size="sm" icon="rotate-right" @click="fetchStatus">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <p v-else-if="status && !status.enabled" class="scrobbling__hint">
      {{ t("pages.scrobbling.disabled") }}
    </p>

    <form
      v-else-if="status && !config"
      class="scrobbling__form"
      @submit.prevent="connect"
    >
      <AppSelect
        v-model="service"
        :options="serviceOptions"
        :label="t('pages.scrobbling.service')"
      />
      <AppInput
        v-model="username"
        :label="t('pages.scrobbling.username')"
        required
      />
      <AppInput
        v-model="password"
        type="password"
        :label="t('pages.scrobbling.password')"
        :hint="t('pages.scrobbling.passwordHint')"
        required
      />
      <div class="scrobbling__actions">
        <AppButton type="submit" :loading="isConnecting">{{
          t("pages.scrobbling.connect")
        }}</AppButton>
      </div>
    </form>

    <form v-else-if="config" class="scrobbling__form" @submit.prevent="save">
      <p class="scrobbling__connected">
        {{
          t("pages.scrobbling.connectedAs", {
            name: config.username,
            service: serviceName,
          })
        }}
      </p>
      <AppCheckbox
        v-model="enabled"
        :label="t('pages.scrobbling.enabled')"
        :hint="t('pages.scrobbling.enabledHint')"
      />
      <AppInput
        v-model="minSeconds"
        type="number"
        min="1"
        max="3600"
        :label="t('pages.scrobbling.minSeconds')"
        :hint="t('pages.scrobbling.minSecondsHint')"
        required
      />
      <AppInput
        v-model="minPercent"
        type="number"
        min="1"
        max="100"
        :label="t('pages.scrobbling.minPercent')"
        :hint="t('pages.scrobbling.minPercentHint')"
        required
      />

      <div class="scrobbling__actions">
        <AppButton type="submit" :loading="isSaving">{{
          t("common.save")
        }}</AppButton>
        <AppButton
          variant="danger"
          icon="trash"
          :loading="isDeleting"
          @click="remove"
        >
          {{ t("pages.scrobbling.disconnect") }}
        </AppButton>
      </div>

      <div class="scrobbling__status">
        <p class="scrobbling__last-scrobbled">
          {{
            t("pages.scrobbling.lastScrobbled", { date: lastScrobbledLabel })
          }}
        </p>
        <p v-if="config.last_error" class="scrobbling__last-error" role="alert">
          {{ t("pages.scrobbling.lastError", { message: config.last_error }) }}
        </p>
      </div>
    </form>
  </section>
</template>

<style scoped>
.scrobbling {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.scrobbling__title {
  margin: 0;
  font-size: 1.25rem;
}

.scrobbling__hint {
  margin: 0;
  color: var(--color-text-muted);
}

.scrobbling__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 32rem;
}

.scrobbling__loading,
.scrobbling__error {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.scrobbling__error {
  color: var(--color-danger);
}

.scrobbling__connected {
  margin: 0;
}

.scrobbling__actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.scrobbling__status {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.scrobbling__last-scrobbled {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.scrobbling__last-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
