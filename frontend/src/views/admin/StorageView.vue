<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useConfirm } from "@/composables/useConfirm";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { triggerStorageCleanup } from "@/api/admin";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const { confirm } = useConfirm();

const isLoading = ref(false);

async function onTrigger() {
  const ok = await confirm({
    title: t("common.confirm"),
    message: t("pages.admin.storage.description"),
    danger: true,
  });
  if (!ok) return;

  isLoading.value = true;
  try {
    await triggerStorageCleanup();
    toastStore.push({
      type: "success",
      message: t("pages.admin.storage.triggered"),
    });
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.storage.triggerError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <div class="storage-view">
    <AppPageTitle icon="database">{{
      t("pages.admin.storage.title")
    }}</AppPageTitle>

    <section class="storage-view__card">
      <p class="storage-view__description">
        {{ t("pages.admin.storage.description") }}
      </p>
      <AppButton
        :loading="isLoading"
        :disabled="isLoading"
        icon="broom"
        @click="onTrigger"
      >
        {{ t("pages.admin.storage.trigger") }}
      </AppButton>
    </section>
  </div>
</template>

<style scoped>
.storage-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.storage-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-6);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  max-width: 40rem;
}

.storage-view__description {
  margin: 0;
  color: var(--color-text-muted);
}
</style>
