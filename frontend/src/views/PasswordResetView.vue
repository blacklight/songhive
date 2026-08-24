<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { passwordResetRequest } from "@/api/auth";
import { getApiErrorMessage } from "@/api/client";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppBanner from "@/components/feedback/AppBanner.vue";

const { t } = useI18n();

const username = ref("");
const isLoading = ref(false);
const success = ref(false);
const error = ref<string | null>(null);

async function onSubmit() {
  if (!username.value) return;

  isLoading.value = true;
  error.value = null;
  success.value = false;

  try {
    await passwordResetRequest({ username: username.value });
    success.value = true;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <form v-if="!success" class="password-reset-view" @submit.prevent="onSubmit">
    <h2 class="password-reset-view__title">
      {{ t("auth.passwordReset.requestTitle") }}
    </h2>

    <p class="password-reset-view__hint">
      {{ t("auth.passwordReset.requestHint") }}
    </p>

    <AppInput
      v-model="username"
      type="text"
      :label="t('auth.passwordReset.usernameOrEmail')"
      :required="true"
      :disabled="isLoading"
    />

    <p
      v-if="error"
      class="password-reset-view__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <AppButton
      type="submit"
      :loading="isLoading"
      class="password-reset-view__submit"
    >
      {{ t("auth.passwordReset.requestSubmit") }}
    </AppButton>

    <nav class="password-reset-view__links">
      <RouterLink to="/login">
        {{ t("auth.passwordReset.backToLogin") }}
      </RouterLink>
    </nav>
  </form>

  <div v-else class="password-reset-view__success">
    <AppBanner type="success" :title="t('auth.passwordReset.requestTitle')">
      {{ t("auth.passwordReset.requestSuccess") }}
    </AppBanner>
    <RouterLink to="/login" class="password-reset-view__back">
      {{ t("auth.passwordReset.backToLogin") }}
    </RouterLink>
  </div>
</template>

<style scoped>
.password-reset-view,
.password-reset-view__success {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.password-reset-view__title {
  margin: 0 0 var(--space-2);
  font-size: 1.5rem;
  text-align: center;
}

.password-reset-view__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.password-reset-view__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.password-reset-view__submit {
  width: 100%;
}

.password-reset-view__links,
.password-reset-view__back {
  font-size: 0.875rem;
  text-align: center;
  color: var(--color-accent-contrast);
}
</style>
