<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useConfirmStore } from "@/stores/confirm";
import AppButton from "@/components/ui/AppButton.vue";
import AppModal from "./AppModal.vue";

const store = useConfirmStore();
const { t } = useI18n();

function cancel() {
  store.cancel();
}

function confirm() {
  store.confirm();
}
</script>

<template>
  <AppModal
    :open="!!store.state?.open"
    :title="store.state?.title"
    @close="cancel"
  >
    <p>{{ store.state?.message }}</p>
    <template #actions>
      <AppButton variant="secondary" icon="xmark" @click="cancel">
        {{ store.state?.cancelLabel || t("common.cancel") }}
      </AppButton>
      <AppButton
        :variant="store.state?.danger ? 'danger' : 'primary'"
        icon="check"
        @click="confirm"
      >
        {{ store.state?.confirmLabel || t("common.confirm") }}
      </AppButton>
    </template>
  </AppModal>
</template>
