<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useEntityList } from "@/composables/useEntityList";
import { useConfirm } from "@/composables/useConfirm";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import {
  listInvites,
  createInvite,
  deleteInvite,
  type AdminInviteResponse,
} from "@/api/admin";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const { confirm } = useConfirm();

const { items, loading, error, hasMore, load, loadMore, refresh } =
  useEntityList<AdminInviteResponse>(
    (params) => listInvites({ limit: params.limit, offset: params.offset }),
    { defaultLimit: 25 },
  );

const isCreateOpen = ref(false);
const isCreating = ref(false);
const isDetailOpen = ref(false);
const maxUses = ref<string | number>("");
const expiresAt = ref("");
const selectedInvite = ref<AdminInviteResponse | null>(null);

function openCreate() {
  isCreateOpen.value = true;
  maxUses.value = "";
  expiresAt.value = "";
}

function closeCreate() {
  isCreateOpen.value = false;
}

function openDetail(invite: AdminInviteResponse) {
  selectedInvite.value = invite;
  isDetailOpen.value = true;
}

function closeDetail() {
  isDetailOpen.value = false;
}

function displayMaxUses(invite: AdminInviteResponse): string {
  return invite.max_uses === null
    ? t("pages.admin.invites.unlimited")
    : String(invite.max_uses);
}

function getInviteUrl(code: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return `${origin}/register?invite_code=${encodeURIComponent(code)}`;
}

async function copyText(text: string, copiedMessage: string) {
  if (!navigator.clipboard) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.invites.copyFailed"),
    });
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toastStore.push({ type: "success", message: copiedMessage });
  } catch {
    toastStore.push({
      type: "error",
      message: t("pages.admin.invites.copyFailed"),
    });
  }
}

function copyInviteUrl(code: string) {
  return copyText(getInviteUrl(code), t("pages.admin.invites.urlCopied"));
}

function copyInviteCode(code: string) {
  return copyText(code, t("pages.admin.invites.tokenCopied"));
}

async function onCreate() {
  if (isCreating.value) return;
  isCreating.value = true;
  try {
    const body = {
      max_uses:
        maxUses.value === "" || maxUses.value === undefined
          ? null
          : Number(maxUses.value),
      expires_at: expiresAt.value || null,
    };
    await createInvite(body);
    toastStore.push({
      type: "success",
      message: t("pages.admin.invites.createSuccess"),
    });
    closeCreate();
    await refresh();
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.invites.actionError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    isCreating.value = false;
  }
}

async function onRevoke(invite: AdminInviteResponse) {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.invites.revokeConfirm", { code: invite.code }),
    danger: true,
  });
  if (!ok) return;

  try {
    await deleteInvite(invite.code);
    toastStore.push({
      type: "success",
      message: t("pages.admin.invites.revokeSuccess"),
    });
    await refresh();
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.invites.actionError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  }
}

async function onModalRevoke() {
  const invite = selectedInvite.value;
  if (!invite) return;
  closeDetail();
  await onRevoke(invite);
}

onMounted(() => load());
</script>

<template>
  <div class="invites-view">
    <header class="invites-view__header">
      <AppPageTitle icon="user-plus">{{
        t("pages.admin.invites.title")
      }}</AppPageTitle>
      <AppButton icon="plus" @click="openCreate">
        {{ t("pages.admin.invites.create") }}
      </AppButton>
    </header>

    <div v-if="error" class="invites-view__error" role="alert">
      {{ error }}
    </div>

    <AppSpinner v-if="loading && items.length === 0" />

    <div
      v-else-if="items.length === 0"
      class="invites-view__empty"
      role="status"
    >
      {{ t("pages.admin.invites.empty") }}
    </div>

    <ul v-else class="invites-view__grid" role="list">
      <li
        v-for="invite in items"
        :key="invite.id"
        class="invites-view__card"
        @click="openDetail(invite)"
      >
        <div class="invites-view__card-header">
          <a
            :href="getInviteUrl(invite.code)"
            target="_blank"
            rel="noopener noreferrer"
            class="invites-view__token"
            :title="invite.code"
            @click.stop
          >
            {{ invite.code }}
          </a>
          <div class="invites-view__card-actions">
            <AppButton
              size="sm"
              variant="ghost"
              icon="copy"
              :aria-label="
                t('common.copy') + ' ' + t('pages.admin.invites.url')
              "
              @click.stop="copyInviteUrl(invite.code)"
            />
            <AppButton
              size="sm"
              variant="danger"
              icon="trash"
              :aria-label="t('pages.admin.invites.revoke')"
              @click.stop="onRevoke(invite)"
            />
          </div>
        </div>

        <dl class="invites-view__card-meta">
          <div>
            <dt>{{ t("pages.admin.invites.maxUses") }}</dt>
            <dd>{{ displayMaxUses(invite) }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.invites.uses") }}</dt>
            <dd>{{ invite.uses }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.invites.expiresAt") }}</dt>
            <dd>
              {{ invite.expires_at ? formatDateTime(invite.expires_at) : "—" }}
            </dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.invites.createdAt") }}</dt>
            <dd>{{ formatDateTime(invite.created_at) }}</dd>
          </div>
        </dl>
      </li>
    </ul>

    <div v-if="hasMore" class="invites-view__load-more">
      <AppButton :loading="loading" :disabled="loading" @click="loadMore">
        {{ t("pages.admin.invites.loadMore") }}
      </AppButton>
    </div>

    <AppModal
      :open="isCreateOpen"
      :title="t('pages.admin.invites.createTitle')"
      @close="closeCreate"
    >
      <form
        id="create-invite-form"
        class="invites-view__create-form"
        @submit.prevent="onCreate"
      >
        <AppInput
          v-model="maxUses"
          type="number"
          :label="t('pages.admin.invites.maxUses')"
          :hint="t('pages.admin.invites.maxUsesHint')"
        />
        <AppInput
          v-model="expiresAt"
          type="datetime-local"
          :label="t('pages.admin.invites.expiresAt')"
          :hint="t('pages.admin.invites.expiresAtHint')"
        />
      </form>

      <template #actions>
        <AppButton variant="secondary" icon="xmark" @click="closeCreate">
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          form="create-invite-form"
          type="submit"
          :loading="isCreating"
          icon="floppy-disk"
        >
          {{ t("common.save") }}
        </AppButton>
      </template>
    </AppModal>

    <AppModal
      :open="isDetailOpen"
      :title="t('pages.admin.invites.detailsTitle')"
      @close="closeDetail"
    >
      <div v-if="selectedInvite" class="invites-view__detail">
        <div class="invites-view__detail-field">
          <AppInput
            :model-value="selectedInvite.code"
            :label="t('pages.admin.invites.token')"
            disabled
          />
          <AppButton
            size="sm"
            icon="copy"
            :aria-label="
              t('common.copy') + ' ' + t('pages.admin.invites.token')
            "
            @click="copyInviteCode(selectedInvite.code)"
          >
            {{ t("common.copy") }}
          </AppButton>
        </div>

        <div class="invites-view__detail-field">
          <AppInput
            :model-value="getInviteUrl(selectedInvite.code)"
            :label="t('pages.admin.invites.url')"
            disabled
          />
          <AppButton
            size="sm"
            icon="copy"
            :aria-label="t('common.copy') + ' ' + t('pages.admin.invites.url')"
            @click="copyInviteUrl(selectedInvite.code)"
          >
            {{ t("common.copy") }}
          </AppButton>
        </div>

        <dl class="invites-view__detail-meta">
          <div>
            <dt>{{ t("pages.admin.invites.maxUses") }}</dt>
            <dd>{{ displayMaxUses(selectedInvite) }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.invites.uses") }}</dt>
            <dd>{{ selectedInvite.uses }}</dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.invites.expiresAt") }}</dt>
            <dd>
              {{
                selectedInvite.expires_at
                  ? formatDateTime(selectedInvite.expires_at)
                  : "—"
              }}
            </dd>
          </div>
          <div>
            <dt>{{ t("pages.admin.invites.createdAt") }}</dt>
            <dd>{{ formatDateTime(selectedInvite.created_at) }}</dd>
          </div>
        </dl>
      </div>

      <template #actions>
        <AppButton variant="secondary" icon="xmark" @click="closeDetail">
          {{ t("common.close") }}
        </AppButton>
        <AppButton variant="danger" icon="trash" @click="onModalRevoke">
          {{ t("pages.admin.invites.revoke") }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.invites-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.invites-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.invites-view__error {
  color: var(--color-danger);
  padding: var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
}

.invites-view__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.invites-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.invites-view__card {
  max-width: 600px;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.invites-view__card:hover {
  background-color: var(--color-surface-hover);
}

.invites-view__card-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.invites-view__token {
  flex: 1 1 auto;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--color-info);
  text-decoration: none;
  font-weight: 500;
}

.invites-view__token:hover {
  text-decoration: underline;
}

.invites-view__card-actions {
  display: flex;
  gap: var(--space-2);
  flex: 0 0 auto;
}

.invites-view__card-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2) var(--space-3);
  margin: 0;
}

.invites-view__card-meta dt {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.invites-view__card-meta dd {
  margin: 0;
  color: var(--color-text);
}

.invites-view__load-more {
  display: flex;
  justify-content: center;
}

.invites-view__create-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.invites-view__detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.invites-view__detail-field {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
}

.invites-view__detail-field :deep(.app-input) {
  flex: 1 1 auto;
  min-width: 0;
}

.invites-view__detail-meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-2) var(--space-3);
  margin: 0;
}

.invites-view__detail-meta dt {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.invites-view__detail-meta dd {
  margin: 0;
  color: var(--color-text);
}
</style>
