<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { passwordResetConfirm } from "@/api/auth";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppBanner from "@/components/feedback/AppBanner.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const toast = useToastStore();

const token = Array.isArray(route.query.token)
  ? route.query.token[0] || ""
  : route.query.token || "";

const newPassword = ref("");
const confirmPassword = ref("");
const error = ref<string | null>(null);
const isLoading = ref(false);

async function onSubmit() {
  if (!token || !newPassword.value) return;

  if (newPassword.value !== confirmPassword.value) {
    error.value = t("auth.passwordReset.passwordMismatch");
    return;
  }

  isLoading.value = true;
  error.value = null;

  try {
    await passwordResetConfirm({ token, new_password: newPassword.value });
    toast.push({
      type: "success",
      message: t("auth.passwordReset.confirmSuccess"),
    });
    await router.replace("/login");
  } catch (err) {
    error.value = getApiErrorMessage(err, t("auth.passwordReset.invalidToken"));
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <form class="password-reset-confirm-view" @submit.prevent="onSubmit">
    <AppPageTitle
      :level="2"
      class="password-reset-confirm-view__title"
      icon="key"
    >
      {{ t("auth.passwordReset.confirmTitle") }}
    </AppPageTitle>

    <AppBanner
      v-if="!token"
      type="error"
      :title="t('auth.passwordReset.confirmTitle')"
    >
      {{ t("auth.passwordReset.invalidToken") }}
    </AppBanner>

    <AppInput
      v-model="newPassword"
      type="password"
      :label="t('auth.passwordReset.newPassword')"
      :required="true"
      :disabled="isLoading || !token"
    />

    <AppInput
      v-model="confirmPassword"
      type="password"
      :label="t('auth.passwordReset.confirmPassword')"
      :required="true"
      :disabled="isLoading || !token"
    />

    <p
      v-if="error"
      class="password-reset-confirm-view__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <AppButton
      type="submit"
      :loading="isLoading"
      :disabled="!token"
      class="password-reset-confirm-view__submit"
      icon="floppy-disk"
    >
      {{ t("auth.passwordReset.confirmSubmit") }}
    </AppButton>

    <nav class="password-reset-confirm-view__links">
      <RouterLink to="/login">
        <AppIcon name="arrow-left" spacing="right" />
        {{ t("auth.passwordReset.backToLogin") }}
      </RouterLink>
    </nav>
  </form>
</template>

<style scoped>
.password-reset-confirm-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.password-reset-confirm-view__title {
  margin: 0 0 var(--space-2);
  font-size: 1.5rem;
  text-align: center;
}

.password-reset-confirm-view__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.password-reset-confirm-view__submit {
  width: 100%;
}

.password-reset-confirm-view__links {
  font-size: 0.875rem;
  text-align: center;
}

.password-reset-confirm-view__links a {
  color: var(--color-accent-contrast);
}
</style>
