<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listSessions,
  revokeSession,
  sha256Hex,
  type SessionSummary,
} from "@/api/auth";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { useConfirm } from "@/composables/useConfirm";
import { useAuthStore } from "@/stores/auth";
import { formatDateTime } from "@/i18n";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toast = useToastStore();
const { confirm } = useConfirm();
const authStore = useAuthStore();

const sessions = ref<SessionSummary[]>([]);
const isLoading = ref(false);
const error = ref<string | null>(null);

function formatDate(value: string | null | undefined): string {
  if (!value) return t("profile.sessions.unknown");
  return formatDateTime(value) || t("profile.sessions.unknown");
}

function formatDevice(value: string | null | undefined): string {
  if (!value) return t("profile.sessions.unknown");
  const trimmed = value.trim();
  if (trimmed.length > 80) {
    return `${trimmed.slice(0, 80)}…`;
  }
  return trimmed;
}

async function fetchSessions() {
  isLoading.value = true;
  error.value = null;
  try {
    const currentId = authStore.refreshToken
      ? await sha256Hex(authStore.refreshToken)
      : "";
    const response = await listSessions(currentId || undefined);
    sessions.value = response.items;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isLoading.value = false;
  }
}

async function revoke(session: SessionSummary) {
  const isCurrent = session.is_current;
  const message = isCurrent
    ? t("profile.sessions.revokeCurrentConfirm")
    : t("profile.sessions.revokeConfirm", {
        device: formatDevice(
          session.user_agent || t("profile.sessions.unknown"),
        ),
      });

  const ok = await confirm({
    title: t("profile.sessions.revoke"),
    message,
    danger: true,
  });
  if (!ok) return;

  try {
    await revokeSession(session.id);
    if (isCurrent) {
      toast.push({
        type: "success",
        message: t("profile.sessions.revokeCurrentSuccess"),
      });
      await authStore.logout();
      return;
    }
    toast.push({
      type: "success",
      message: t("profile.sessions.revokeSuccess"),
    });
    await fetchSessions();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  }
}

onMounted(fetchSessions);
</script>

<template>
  <div class="sessions-tab">
    <p v-if="error" class="sessions-tab__error" role="alert" aria-live="polite">
      {{ error }}
    </p>

    <div v-if="isLoading" class="sessions-tab__loading">
      <AppSpinner />
    </div>

    <template v-else-if="sessions.length > 0">
      <ul class="sessions-tab__cards" role="list">
        <li
          v-for="session in sessions"
          :key="session.id"
          class="sessions-tab__card"
          :class="{ 'sessions-tab__card--current': session.is_current }"
        >
          <div class="sessions-tab__card-header">
            <span class="sessions-tab__card-device">
              {{ formatDevice(session.user_agent) }}
            </span>
            <span v-if="session.is_current" class="sessions-tab__badge">
              {{ t("profile.sessions.current") }}
            </span>
          </div>

          <dl class="sessions-tab__card-body">
            <div>
              <dt>{{ t("profile.sessions.ipAddress") }}</dt>
              <dd>
                {{ session.ip_address || t("profile.sessions.unknown") }}
              </dd>
            </div>
            <div>
              <dt>{{ t("profile.sessions.createdAt") }}</dt>
              <dd>{{ formatDate(session.created_at) }}</dd>
            </div>
            <div>
              <dt>{{ t("profile.sessions.expiresAt") }}</dt>
              <dd>{{ formatDate(session.expires_at) }}</dd>
            </div>
          </dl>

          <div class="sessions-tab__card-footer">
            <AppButton
              type="button"
              variant="danger"
              size="sm"
              icon="trash-can"
              @click="revoke(session)"
            >
              {{ t("profile.sessions.revoke") }}
            </AppButton>
          </div>
        </li>
      </ul>
    </template>

    <div v-else class="sessions-tab__empty" role="status">
      {{ t("profile.sessions.empty") }}
    </div>
  </div>
</template>

<style scoped>
.sessions-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.sessions-tab__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.sessions-tab__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.sessions-tab__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.sessions-tab__cards {
  display: grid;
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.sessions-tab__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.sessions-tab__card--current {
  border-color: var(--color-accent);
}

.sessions-tab__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  min-width: 0;
}

.sessions-tab__card-device {
  font-weight: 600;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.sessions-tab__badge {
  flex: 0 0 auto;
  display: inline-block;
  padding: var(--space-0) var(--space-2);
  border-radius: var(--radius-sm);
  background-color: var(--color-accent);
  color: var(--color-surface);
  font-size: 0.75rem;
  font-weight: 500;
}

.sessions-tab__card-body {
  display: grid;
  gap: var(--space-2);
  margin: 0;
}

.sessions-tab__card-body div {
  display: grid;
  grid-template-columns: 7rem 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.sessions-tab__card-body dt {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  font-weight: 500;
}

.sessions-tab__card-body dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.sessions-tab__card-footer {
  display: flex;
  justify-content: flex-end;
}

@media (min-width: 600px) {
  .sessions-tab__cards {
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
  }
}
</style>
