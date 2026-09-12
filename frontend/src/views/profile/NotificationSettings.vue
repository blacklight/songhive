<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  getNotificationPreferences,
  updateNotificationPreferences,
  NOTIFICATION_TYPES,
  type NotificationPreferenceItem,
  type NotificationType,
} from "@/api/notifications";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toast = useToastStore();
const authStore = useAuthStore();

const preferences = ref<NotificationPreferenceItem[]>([]);
const isLoading = ref(false);
const isSaving = ref(false);
const error = ref<string | null>(null);

const emailVerified = computed(() => authStore.user?.email_verified === true);

function prefFor(type: NotificationType): NotificationPreferenceItem {
  let pref = preferences.value.find((p) => p.type === type);
  if (!pref) {
    pref = { type, in_app: true, email: false, email_digest: false };
    preferences.value.push(pref);
  }
  return pref;
}

async function fetchPreferences() {
  isLoading.value = true;
  error.value = null;
  try {
    preferences.value = await getNotificationPreferences();
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      t("notifications.settings.loadFailed", {
        message: err instanceof Error ? err.message : "",
      });
  } finally {
    isLoading.value = false;
  }
}

async function save() {
  isSaving.value = true;
  try {
    preferences.value = await updateNotificationPreferences(preferences.value);
    toast.push({
      type: "success",
      message: t("notifications.settings.saved"),
    });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("notifications.settings.saveFailed", {
        message:
          getApiErrorMessage(err) || (err instanceof Error ? err.message : ""),
      }),
    });
  } finally {
    isSaving.value = false;
  }
}

onMounted(fetchPreferences);
</script>

<template>
  <section class="notification-settings">
    <h2 class="notification-settings__title">
      {{ t("notifications.settings.title") }}
    </h2>
    <p class="notification-settings__hint">
      {{ t("notifications.settings.hint") }}
    </p>

    <p
      v-if="!emailVerified"
      class="notification-settings__email-warning"
      role="note"
    >
      {{ t("notifications.settings.emailUnverified") }}
    </p>

    <div v-if="isLoading" class="notification-settings__loading">
      <AppSpinner />
    </div>

    <div v-else-if="error" class="notification-settings__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="fetchPreferences">{{
        t("common.retry")
      }}</AppButton>
    </div>

    <template v-else>
      <table class="notification-settings__matrix">
        <thead>
          <tr>
            <th scope="col">{{ t("notifications.settings.type") }}</th>
            <th scope="col">{{ t("notifications.settings.inApp") }}</th>
            <th scope="col">{{ t("notifications.settings.email") }}</th>
            <th scope="col">
              {{ t("notifications.settings.emailDigest") }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="type in NOTIFICATION_TYPES" :key="type">
            <th scope="row">
              {{ t(`notifications.settings.typeNames.${type}`) }}
            </th>
            <td>
              <AppCheckbox
                v-model="prefFor(type).in_app"
                :aria-label="`${t(`notifications.settings.typeNames.${type}`)} — ${t('notifications.settings.inApp')}`"
              />
            </td>
            <td>
              <AppCheckbox
                v-model="prefFor(type).email"
                :disabled="!emailVerified"
                :aria-label="`${t(`notifications.settings.typeNames.${type}`)} — ${t('notifications.settings.email')}`"
              />
            </td>
            <td>
              <AppCheckbox
                v-model="prefFor(type).email_digest"
                :disabled="!emailVerified"
                :aria-label="`${t(`notifications.settings.typeNames.${type}`)} — ${t('notifications.settings.emailDigest')}`"
              />
            </td>
          </tr>
        </tbody>
      </table>

      <AppButton
        class="notification-settings__save"
        :loading="isSaving"
        @click="save"
      >
        {{ t("notifications.settings.save") }}
      </AppButton>
    </template>
  </section>
</template>

<style scoped>
.notification-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.notification-settings__title {
  margin: 0;
  font-size: 1.25rem;
}

.notification-settings__hint {
  margin: 0;
  color: var(--color-text-muted);
}

.notification-settings__email-warning {
  margin: 0;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.notification-settings__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.notification-settings__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.notification-settings__matrix {
  border-collapse: collapse;
}

.notification-settings__matrix th,
.notification-settings__matrix td {
  padding: var(--space-2) var(--space-3);
  text-align: center;
}

.notification-settings__matrix th[scope="row"] {
  text-align: left;
  font-weight: 500;
}

.notification-settings__save {
  align-self: flex-start;
}
</style>
