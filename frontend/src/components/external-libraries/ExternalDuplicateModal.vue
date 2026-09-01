<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import AppModal from "@/components/feedback/AppModal.vue";
import AppButton from "@/components/ui/AppButton.vue";
import {
  resolveUploadDuplicate,
  type ExternalDuplicateWarning,
} from "@/api/externalLibraries";
import { getApiErrorMessage, ApiError } from "@/api/client";

export interface Props {
  open: boolean;
  warning: ExternalDuplicateWarning | null;
}

const props = defineProps<Props>();
const emit = defineEmits<{ close: []; resolved: [result: unknown] }>();

const { t } = useI18n();
const isResolving = ref(false);
const error = ref<string | null>(null);

const provider = computed(() => props.warning?.provider_type ?? "");

function formatInfo(info: Record<string, unknown>): string {
  const parts = Object.entries(info).map(
    ([key, value]) => `${key}: ${String(value)}`,
  );
  return parts.join("; ");
}

async function resolve(action: "keep_local" | "discard_upload") {
  if (!props.warning) return;

  isResolving.value = true;
  error.value = null;
  try {
    const result = await resolveUploadDuplicate(props.warning.token, action);
    emit("resolved", result);
  } catch (err) {
    const message = getApiErrorMessage(err) ?? t("errors.unknown");
    error.value = t("pages.externalLibraries.resolveError", { message });
    if (err instanceof ApiError && err.status === 409) {
      // A different or refreshed conflict; just surface the message.
      error.value = message;
    }
  } finally {
    isResolving.value = false;
  }
}

function close() {
  if (!isResolving.value) emit("close");
}
</script>

<template>
  <AppModal
    :open="open"
    :title="t('pages.externalLibraries.duplicateModalTitle')"
    :closable="!isResolving"
    @close="close"
  >
    <p class="external-duplicate-modal__message">
      {{
        t("pages.externalLibraries.duplicateModalMessage", {
          provider: provider || t("pages.externalLibraries.title"),
        })
      }}
    </p>

    <ul
      v-if="warning?.display_info.length"
      class="external-duplicate-modal__details"
    >
      <li v-for="(info, index) in warning.display_info" :key="index">
        {{ formatInfo(info) }}
      </li>
    </ul>

    <p v-if="error" class="external-duplicate-modal__error" role="alert">
      {{ error }}
    </p>

    <template #actions>
      <AppButton
        variant="secondary"
        :disabled="isResolving"
        icon="xmark"
        @click="close"
      >
        {{ t("common.cancel") }}
      </AppButton>
      <AppButton
        data-testid="keep-local-button"
        variant="secondary"
        :loading="isResolving"
        icon="cloud"
        @click="resolve('keep_local')"
      >
        {{ t("pages.externalLibraries.keepLocal") }}
      </AppButton>
      <AppButton
        data-testid="discard-upload-button"
        variant="danger"
        :loading="isResolving"
        icon="trash"
        @click="resolve('discard_upload')"
      >
        {{ t("pages.externalLibraries.discardUpload") }}
      </AppButton>
    </template>
  </AppModal>
</template>

<style scoped>
.external-duplicate-modal__message {
  margin: 0 0 var(--space-3);
}

.external-duplicate-modal__details {
  margin: 0;
  padding-left: var(--space-4);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.external-duplicate-modal__details li {
  margin-bottom: var(--space-1);
}

.external-duplicate-modal__error {
  margin: var(--space-3) 0 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
