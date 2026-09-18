<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";

export interface Props {
  modelValue: boolean;
}

defineProps<Props>();

const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

const { t } = useI18n();
const authStore = useAuthStore();
</script>

<template>
  <AppCheckbox
    v-if="authStore.isAuthenticated"
    :model-value="modelValue"
    :label="t('browse.list.onlyMine')"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
