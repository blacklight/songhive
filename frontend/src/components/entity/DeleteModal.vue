<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import AppModal from "@/components/feedback/AppModal.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";

export interface Props {
  open: boolean;
  title: string;
  message: string;
  allowRecursive?: boolean;
  recursiveLabel?: string;
  loading?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  allowRecursive: false,
  recursiveLabel: undefined,
  loading: false,
});

const emit = defineEmits<{
  close: [];
  confirm: [recursive: boolean];
}>();

const { t } = useI18n();
const recursive = ref(false);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      recursive.value = false;
    }
  },
);

function close() {
  emit("close");
}

function confirm() {
  emit("confirm", recursive.value);
}
</script>

<template>
  <AppModal :open="open" :title="title" @close="close">
    <p class="delete-modal__message">{{ message }}</p>
    <AppCheckbox
      v-if="allowRecursive"
      v-model="recursive"
      class="delete-modal__recursive"
      :label="recursiveLabel"
    />

    <template #actions>
      <AppButton variant="secondary" icon="xmark" @click="close">
        {{ t("common.cancel") }}
      </AppButton>
      <AppButton
        variant="danger"
        icon="trash"
        :loading="loading"
        :disabled="loading"
        @click="confirm"
      >
        {{ t("common.delete") }}
      </AppButton>
    </template>
  </AppModal>
</template>

<style scoped>
.delete-modal__message {
  margin: 0;
}

.delete-modal__recursive {
  margin-top: var(--space-3);
}
</style>
