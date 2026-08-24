<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listApiTokens,
  createApiToken,
  revokeApiToken,
} from "@/api/auth";
import { ApiError } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { useConfirm } from "@/composables/useConfirm";
import { formatDateTime } from "@/i18n";
import type { components } from "@/api/types";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppTable from "@/components/ui/AppTable.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toast = useToastStore();
const { confirm } = useConfirm();

type ApiTokenSummary = components["schemas"]["ApiTokenSummary"];

const tokens = ref<ApiTokenSummary[]>([]);
const isLoading = ref(false);
const isCreating = ref(false);
const error = ref<string | null>(null);
const name = ref("");
const expiresAt = ref("");
const newToken = ref<string | null>(null);
const rawTokenInput = ref<HTMLInputElement | null>(null);

function selectRawToken() {
  rawTokenInput.value?.select();
}

const columns = [
  { key: "name", label: t("profile.apiTokens.name") },
  { key: "created_at", label: t("profile.apiTokens.createdAt") },
  { key: "expires_at", label: t("profile.apiTokens.expiresAt") },
  { key: "last_used_at", label: t("profile.apiTokens.lastUsedAt") },
  { key: "actions", label: "", align: "right" as const },
];

async function fetchTokens() {
  isLoading.value = true;
  try {
    const response = await listApiTokens();
    tokens.value = response.items;
  } catch (err) {
    const message =
      err instanceof ApiError ? err.detail || err.message : undefined;
    error.value = message || t("errors.unknown");
  } finally {
    isLoading.value = false;
  }
}

function formatDate(value: string | null | undefined): string {
  if (!value) return t("profile.apiTokens.never");
  return formatDateTime(value) || t("profile.apiTokens.never");
}

async function onSubmit() {
  error.value = null;

  let expires: string | undefined;
  if (expiresAt.value) {
    const parsed = new Date(expiresAt.value);
    if (Number.isNaN(parsed.getTime())) {
      error.value = t("errors.unknown");
      return;
    }
    if (parsed.getTime() <= Date.now()) {
      error.value = t("errors.unknown");
      return;
    }
    expires = parsed.toISOString();
  }

  isCreating.value = true;
  try {
    const response = await createApiToken(
      expires ? { name: name.value, expires_at: expires } : { name: name.value },
    );
    newToken.value = response.token;
    name.value = "";
    expiresAt.value = "";
    await fetchTokens();
  } catch (err) {
    const message =
      err instanceof ApiError ? err.detail || err.message : undefined;
    error.value = message || t("errors.unknown");
  } finally {
    isCreating.value = false;
  }
}

async function copyToken() {
  if (!newToken.value) return;
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(newToken.value);
  }
  toast.push({ type: "success", message: t("profile.apiTokens.tokenCopied") });
}

async function revoke(id: string, tokenName: string) {
  const ok = await confirm({
    title: t("profile.apiTokens.revoke"),
    message: t("profile.apiTokens.revoke") + ` "${tokenName}"?`,
    danger: true,
  });
  if (!ok) return;

  try {
    await revokeApiToken(id);
    await fetchTokens();
  } catch (err) {
    const message =
      err instanceof ApiError ? err.detail || err.message : undefined;
    error.value = message || t("errors.unknown");
  }
}

onMounted(fetchTokens);
</script>

<template>
  <div class="api-tokens-tab">
    <form class="api-tokens-tab__create" @submit.prevent="onSubmit">
      <AppInput
        v-model="name"
        type="text"
        :label="t('profile.apiTokens.name')"
        :hint="t('profile.apiTokens.nameHint')"
        :required="true"
        :disabled="isCreating"
      />

      <AppInput
        v-model="expiresAt"
        type="datetime-local"
        :label="t('profile.apiTokens.expiresAt')"
        :hint="t('profile.apiTokens.expiresAtHint')"
        :disabled="isCreating"
      />

      <AppButton
        type="submit"
        :loading="isCreating"
      >
        {{ t("profile.apiTokens.create") }}
      </AppButton>

      <p
        v-if="error"
        class="api-tokens-tab__error"
        role="alert"
        aria-live="polite"
      >
        {{ error }}
      </p>
    </form>

    <div v-if="newToken" class="api-tokens-tab__raw">
      <label for="raw-token" class="api-tokens-tab__label">
        {{ t("profile.apiTokens.tokenCopied") }}
      </label>
      <div class="api-tokens-tab__raw-field">
        <input
          id="raw-token"
          ref="rawTokenInput"
          :value="newToken"
          type="text"
          readonly
          class="api-tokens-tab__raw-input"
          @focus="selectRawToken"
        />
        <AppButton type="button" @click="copyToken">
          {{ t("common.copy") }}
        </AppButton>
      </div>
      <p class="api-tokens-tab__hint">{{ t("profile.apiTokens.rawTokenHint") }}</p>
    </div>

    <div class="api-tokens-tab__list">
      <AppSpinner v-if="isLoading" />

      <AppTable
        v-else
        :columns="columns"
        :rows="tokens"
        :empty-label="t('profile.apiTokens.empty')"
      >
        <template #row-created_at="{ value }">
          {{ formatDate(value as string) }}
        </template>
        <template #row-expires_at="{ value }">
          {{ formatDate(value as string) }}
        </template>
        <template #row-last_used_at="{ value }">
          {{ formatDate(value as string) }}
        </template>
        <template #row-actions="{ row }">
          <AppButton
            type="button"
            variant="danger"
            size="sm"
            @click="revoke((row as ApiTokenSummary).id, (row as ApiTokenSummary).name)"
          >
            {{ t("profile.apiTokens.revoke") }}
          </AppButton>
        </template>
      </AppTable>
    </div>
  </div>
</template>

<style scoped>
.api-tokens-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.api-tokens-tab__create {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex: 1;
}

.api-tokens-tab__error {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.api-tokens-tab__raw {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
}

.api-tokens-tab__label {
  font-weight: 600;
  color: var(--color-text);
}

.api-tokens-tab__raw-field {
  display: flex;
  gap: var(--space-2);
}

.api-tokens-tab__raw-input {
  flex: 1;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-family: inherit;
}

.api-tokens-tab__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.api-tokens-tab__list {
  min-height: 10rem;
}
</style>
