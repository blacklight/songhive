<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { register } from "@/api/auth";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();
const router = useRouter();
const toast = useToastStore();

const username = ref("");
const email = ref("");
const password = ref("");
const confirmPassword = ref("");
const displayName = ref("");
const inviteCode = ref("");
const error = ref<string | null>(null);
const isLoading = ref(false);

async function onSubmit() {
  error.value = null;

  if (!username.value || !email.value || !password.value) {
    return;
  }

  if (password.value !== confirmPassword.value) {
    error.value = t("auth.registerPage.passwordMismatch");
    return;
  }

  const payload: Parameters<typeof register>[0] = {
    username: username.value,
    email: email.value,
    password: password.value,
  };

  if (displayName.value.trim()) {
    payload.display_name = displayName.value.trim();
  }

  if (inviteCode.value.trim()) {
    payload.invite_code = inviteCode.value.trim();
  }

  isLoading.value = true;
  try {
    const response = await register(payload);
    toast.push({
      type: "success",
      message: t("auth.registerPage.success"),
    });
    if (!response.email_verified) {
      toast.push({
        type: "info",
        message: t("auth.registerPage.emailVerificationNotice"),
      });
    }
    await router.replace("/login");
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <form class="register-view" @submit.prevent="onSubmit">
    <AppPageTitle :level="2" class="register-view__title" icon="user-plus">
      {{ t("auth.registerPage.title") }}
    </AppPageTitle>

    <AppInput
      v-model="username"
      type="text"
      :label="t('auth.registerPage.username')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="email"
      type="email"
      :label="t('auth.registerPage.email')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="password"
      type="password"
      :label="t('auth.registerPage.password')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="confirmPassword"
      type="password"
      :label="t('auth.registerPage.confirmPassword')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="displayName"
      type="text"
      :label="t('auth.registerPage.displayName')"
      :hint="t('auth.registerPage.displayNameHint')"
      :disabled="isLoading"
    />

    <AppInput
      v-model="inviteCode"
      type="text"
      :label="t('auth.registerPage.inviteCode')"
      :hint="t('auth.registerPage.inviteCodeHint')"
      :disabled="isLoading"
    />

    <p
      v-if="error"
      class="register-view__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <AppButton
      type="submit"
      :loading="isLoading"
      class="register-view__submit"
      icon="user-plus"
    >
      {{ t("auth.registerPage.submit") }}
    </AppButton>

    <nav class="register-view__links">
      <RouterLink to="/login">
        {{ t("auth.registerPage.haveAccount") }}
      </RouterLink>
    </nav>
  </form>
</template>

<style scoped>
.register-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.register-view__title {
  margin: 0 0 var(--space-2);
  font-size: 1.5rem;
  text-align: center;
}

.register-view__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.register-view__submit {
  width: 100%;
}

.register-view__links {
  font-size: 0.875rem;
  text-align: center;
}

.register-view__links a {
  color: var(--color-accent-contrast);
}
</style>
