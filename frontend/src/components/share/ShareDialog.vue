<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  listShareGrants,
  createShareGrant,
  deleteShareGrant,
  listShareUrls,
  createShareUrl,
  deleteShareUrl,
  type ShareItemType,
  type ShareGrantResponse,
  type ShareTokenResponse,
} from "@/api/shares";
import { getApiErrorMessage, ApiError } from "@/api/client";
import { useOwnership } from "@/composables/useOwnership";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppModal from "@/components/feedback/AppModal.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppTable from "@/components/ui/AppTable.vue";

export interface Props {
  open: boolean;
  itemType: ShareItemType;
  itemId: string;
  title: string;
  ownerId?: string | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();

const { t } = useI18n();
const confirm = useConfirmStore();
const toast = useToastStore();
const { isOwner } = useOwnership(computed(() => props.ownerId ?? null));

const activeTab = ref<"grants" | "urls">("grants");

const grants = ref<ShareGrantResponse[]>([]);
const grantsLoading = ref(false);
const grantsError = ref<string | null>(null);

const urls = ref<ShareTokenResponse[]>([]);
const urlsLoading = ref(false);
const urlsError = ref<string | null>(null);

const userId = ref("");
const expiresAt = ref("");
const isCreatingGrant = ref(false);
const isCreatingUrl = ref(false);

const newUrl = ref<string | null>(null);
const newToken = ref<string | null>(null);

const grantColumns = [
  { key: "user_id", label: t("browse.share.user") },
  { key: "createdAt", label: t("browse.share.createdAt") },
  { key: "actions", label: t("browse.detail.actions") },
];

const urlColumns = [
  { key: "expiresAt", label: t("browse.share.expiresAt") },
  { key: "createdAt", label: t("browse.share.createdAt") },
  { key: "actions", label: t("browse.detail.actions") },
];

const grantRows = computed(() =>
  grants.value.map((grant) => ({
    id: grant.id,
    user_id: grant.user_id,
    createdAt: formatDateTime(grant.created_at),
    actions: "",
  })),
);

const urlRows = computed(() =>
  urls.value.map((token) => ({
    id: token.id,
    expiresAt: token.expires_at ? formatDateTime(token.expires_at) : "—",
    createdAt: formatDateTime(token.created_at),
    actions: "",
  })),
);

function rowKey(row: Record<string, unknown>) {
  return String(row.id);
}

function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 403) {
    return t("pages.forbidden");
  }
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function loadGrants() {
  grantsLoading.value = true;
  grantsError.value = null;
  try {
    grants.value = await listShareGrants({
      item_type: props.itemType,
      item_id: props.itemId,
      limit: 100,
    });
  } catch (err) {
    grantsError.value = getErrorMessage(err);
  } finally {
    grantsLoading.value = false;
  }
}

async function loadUrls() {
  urlsLoading.value = true;
  urlsError.value = null;
  try {
    urls.value = await listShareUrls({
      item_type: props.itemType,
      item_id: props.itemId,
      limit: 100,
    });
  } catch (err) {
    urlsError.value = getErrorMessage(err);
  } finally {
    urlsLoading.value = false;
  }
}

function load() {
  if (activeTab.value === "grants") {
    loadGrants();
  } else {
    loadUrls();
  }
}

async function createGrant() {
  const targetUser = userId.value.trim();
  if (!targetUser) return;

  isCreatingGrant.value = true;
  grantsError.value = null;
  try {
    await createShareGrant({
      item_type: props.itemType,
      item_id: props.itemId,
      user_id: targetUser,
    });
    userId.value = "";
    toast.push({ type: "success", message: t("browse.share.grantCreated") });
    await loadGrants();
  } catch (err) {
    grantsError.value = t("browse.share.grantCreateError", {
      message: getErrorMessage(err),
    });
  } finally {
    isCreatingGrant.value = false;
  }
}

function isFutureDate(value: string): boolean {
  if (!value) return true;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return false;
  return date.getTime() > Date.now();
}

async function createUrl() {
  if (!isFutureDate(expiresAt.value)) {
    urlsError.value = t("browse.share.expiresAtInvalid");
    return;
  }

  isCreatingUrl.value = true;
  urlsError.value = null;
  try {
    const response = await createShareUrl({
      item_type: props.itemType,
      item_id: props.itemId,
      expires_at: expiresAt.value
        ? new Date(expiresAt.value).toISOString()
        : null,
    });
    newUrl.value = response.url;
    newToken.value = response.token;
    expiresAt.value = "";
    toast.push({ type: "success", message: t("browse.share.shareUrlCreated") });
    await loadUrls();
  } catch (err) {
    urlsError.value = t("browse.share.urlCreateError", {
      message: getErrorMessage(err),
    });
  } finally {
    isCreatingUrl.value = false;
  }
}

async function revokeGrant(grantId: string) {
  const grant = grants.value.find((g) => g.id === grantId);
  if (!grant) return;

  const confirmed = await confirm.open({
    title: t("browse.share.revoke"),
    message: t("browse.share.revokeConfirm", { user: grant.user_id }),
    danger: true,
    confirmLabel: t("browse.share.revoke"),
  });
  if (!confirmed) return;

  grantsError.value = null;
  try {
    await deleteShareGrant(grant.id);
    await loadGrants();
  } catch (err) {
    grantsError.value = t("browse.share.revokeError", {
      message: getErrorMessage(err),
    });
  }
}

async function revokeUrl(tokenId: string) {
  const token = urls.value.find((u) => u.id === tokenId);
  if (!token) return;

  const confirmed = await confirm.open({
    title: t("browse.share.revoke"),
    message: t("browse.share.revokeUrlConfirm"),
    danger: true,
    confirmLabel: t("browse.share.revoke"),
  });
  if (!confirmed) return;

  urlsError.value = null;
  try {
    await deleteShareUrl(token.id);
    await loadUrls();
  } catch (err) {
    urlsError.value = t("browse.share.revokeError", {
      message: getErrorMessage(err),
    });
  }
}

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.push({ type: "success", message: t("browse.share.urlCopied") });
  } catch {
    toast.push({ type: "error", message: t("browse.share.copyFailed") });
  }
}

function close() {
  emit("close");
}

watch(
  [() => props.open, () => props.itemId, activeTab],
  () => {
    if (props.open) {
      load();
    }
  },
  { immediate: true },
);

watch(
  () => props.open,
  (open) => {
    if (!open) {
      userId.value = "";
      expiresAt.value = "";
      newUrl.value = null;
      newToken.value = null;
      grantsError.value = null;
      urlsError.value = null;
    }
  },
);
</script>

<template>
  <AppModal
    :open="props.open"
    :title="t('browse.share.shareTitle', { name: props.title })"
    @close="close"
  >
    <div class="share-dialog__tabs">
      <AppButton
        size="sm"
        icon="users"
        :variant="activeTab === 'grants' ? 'primary' : 'ghost'"
        @click="activeTab = 'grants'"
      >
        {{ t("browse.share.shareGrants") }}
      </AppButton>
      <AppButton
        size="sm"
        icon="link"
        :variant="activeTab === 'urls' ? 'primary' : 'ghost'"
        @click="activeTab = 'urls'"
      >
        {{ t("browse.share.shareUrls") }}
      </AppButton>
    </div>

    <div v-if="activeTab === 'grants'" class="share-dialog__panel">
      <div v-if="isOwner" class="share-dialog__form">
        <AppInput
          v-model="userId"
          :label="t('browse.share.user')"
          :required="true"
        />
        <AppButton
          size="sm"
          icon="plus"
          :loading="isCreatingGrant"
          :disabled="!userId.trim()"
          @click="createGrant"
        >
          {{ t("browse.share.createGrant") }}
        </AppButton>
      </div>

      <div v-if="grantsError" class="share-dialog__error" role="alert">
        {{ grantsError }}
        <AppButton size="sm" icon="rotate-right" @click="loadGrants">
          {{ t("common.retry") }}
        </AppButton>
      </div>

      <AppTable
        :columns="grantColumns"
        :rows="grantRows"
        :row-key="rowKey"
        :loading="grantsLoading"
        :empty-label="t('browse.share.emptyGrants')"
      >
        <template #row-actions="{ row }">
          <AppButton
            size="sm"
            variant="danger"
            icon="trash-can"
            @click="revokeGrant(String(row.id))"
          >
            {{ t("browse.share.revoke") }}
          </AppButton>
        </template>
      </AppTable>
    </div>

    <div v-else class="share-dialog__panel">
      <div v-if="newUrl" class="share-dialog__new-url">
        <AppInput
          :model-value="newUrl"
          :label="t('browse.share.copyUrl')"
          disabled
        />
        <AppButton size="sm" icon="copy" @click="copyToClipboard(newUrl)">
          {{ t("common.copy") }}
        </AppButton>

        <AppInput
          :model-value="newToken ?? ''"
          :label="t('browse.share.rawToken')"
          :hint="t('browse.share.rawTokenHint')"
          disabled
        />
        <AppButton
          size="sm"
          icon="copy"
          @click="newToken && copyToClipboard(newToken)"
        >
          {{ t("common.copy") }}
        </AppButton>
      </div>

      <div v-if="isOwner" class="share-dialog__form">
        <AppInput
          v-model="expiresAt"
          type="datetime-local"
          :label="t('browse.share.expiresAt')"
          :hint="t('browse.share.expiresAtHint')"
        />
        <AppButton
          size="sm"
          icon="plus"
          :loading="isCreatingUrl"
          @click="createUrl"
        >
          {{ t("browse.share.createShareUrl") }}
        </AppButton>
      </div>

      <div v-if="urlsError" class="share-dialog__error" role="alert">
        {{ urlsError }}
        <AppButton size="sm" icon="rotate-right" @click="loadUrls">
          {{ t("common.retry") }}
        </AppButton>
      </div>

      <AppTable
        :columns="urlColumns"
        :rows="urlRows"
        :row-key="rowKey"
        :loading="urlsLoading"
        :empty-label="t('browse.share.emptyUrls')"
      >
        <template #row-actions="{ row }">
          <AppButton
            size="sm"
            variant="danger"
            icon="trash-can"
            @click="revokeUrl(String(row.id))"
          >
            {{ t("browse.share.revoke") }}
          </AppButton>
        </template>
      </AppTable>
    </div>
  </AppModal>
</template>

<style scoped>
.share-dialog__tabs {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.share-dialog__panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.share-dialog__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.share-dialog__new-url {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface-secondary);
}

.share-dialog__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}
</style>
