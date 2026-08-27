<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { changePassword } from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";

const { t } = useI18n();
const router = useRouter();
const authStore = useAuthStore();
const toast = useToastStore();

const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const isLoading = ref(false);
const error = ref<string | null>(null);

const MAX_PASSWORD_BYTES = 72;

function validateForm(): boolean {
  if (
    !currentPassword.value.trim() ||
    !newPassword.value ||
    !confirmPassword.value
  ) {
    error.value = t("profile.passwordRequired");
    return false;
  }

  if (new TextEncoder().encode(newPassword.value).length > MAX_PASSWORD_BYTES) {
    error.value = t("profile.passwordTooLong");
    return false;
  }

  if (newPassword.value !== confirmPassword.value) {
    error.value = t("profile.passwordMismatch");
    return false;
  }

  return true;
}

async function onSubmit() {
  error.value = null;

  if (!validateForm()) {
    return;
  }

  isLoading.value = true;

  try {
    await changePassword({
      current_password: currentPassword.value,
      new_password: newPassword.value,
    });

    toast.push({
      type: "success",
      message: t("profile.passwordChangeSuccess"),
    });

    currentPassword.value = "";
    newPassword.value = "";
    confirmPassword.value = "";

    await authStore.logout();
    await router.push("/login");
  } catch (err) {
    error.value = t("profile.passwordChangeError", {
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <form class="change-password-tab" @submit.prevent="onSubmit">
    <AppInput
      v-model="currentPassword"
      type="password"
      :label="t('profile.currentPassword')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="newPassword"
      type="password"
      :label="t('profile.newPassword')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="confirmPassword"
      type="password"
      :label="t('profile.confirmPassword')"
      :required="true"
      :disabled="isLoading"
    />

    <p
      v-if="error"
      class="change-password-tab__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <AppButton type="submit" :loading="isLoading" icon="floppy-disk">
      {{ t("profile.changePassword") }}
    </AppButton>
  </form>
</template>

<style scoped>
.change-password-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.change-password-tab__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
