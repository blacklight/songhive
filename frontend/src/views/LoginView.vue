<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import type { MfaChallenge } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import { ApiError, getApiErrorMessage } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const instanceStore = useInstanceStore();

const showRegisterLink = computed(() => instanceStore.registrations);

const username = ref("");
const password = ref("");
const error = ref<string | null>(null);
const isLoading = computed(() => authStore.status === "loading");

// Second-factor step, shown after a successful password check that returned
// an mfa challenge instead of a session.
const mfaChallenge = ref<MfaChallenge | null>(null);
const mfaCode = ref("");
const supportsCode = computed(
  () =>
    !!mfaChallenge.value &&
    (mfaChallenge.value.methods.includes("totp") ||
      mfaChallenge.value.methods.includes("recovery_code")),
);
const supportsSecurityKey = computed(
  () => !!mfaChallenge.value?.methods.includes("webauthn"),
);

function safeRedirect(raw: unknown): string {
  let value = "";
  if (Array.isArray(raw)) {
    value = raw[0] ?? "";
  } else if (typeof raw === "string") {
    value = raw;
  }
  // Reject protocol-relative and external URLs; only allow a single leading slash.
  if (value && value.startsWith("/") && !value.startsWith("//")) {
    return value;
  }
  return "/";
}

async function finishLogin() {
  const target = safeRedirect(route.query.redirect);
  await router.replace(target);
}

async function onSubmit() {
  error.value = null;
  if (!username.value || !password.value) return;

  try {
    const challenge = await authStore.login(username.value, password.value);
    if (challenge) {
      mfaChallenge.value = challenge;
      return;
    }
    await finishLogin();
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      error.value = t("auth.loginPage.emailNotVerified");
    } else {
      error.value = t("auth.loginPage.failed");
    }
  }
}

async function onMfaCodeSubmit() {
  error.value = null;
  if (!mfaChallenge.value || !mfaCode.value) return;
  const token = mfaChallenge.value.mfaToken;

  try {
    await authStore.loginWithMfaCode(token, mfaCode.value);
    await finishLogin();
  } catch (err) {
    // The pending login is single-use: a wrong code drops it, so the user
    // has to sign in again.
    mfaChallenge.value = null;
    mfaCode.value = "";
    password.value = "";
    error.value = getApiErrorMessage(err) || t("auth.loginPage.mfa.failed");
  }
}

async function onMfaSecurityKey() {
  error.value = null;
  if (!mfaChallenge.value) return;
  const token = mfaChallenge.value.mfaToken;

  try {
    await authStore.loginWithMfaSecurityKey(token);
    await finishLogin();
  } catch (err) {
    mfaChallenge.value = null;
    password.value = "";
    error.value = getApiErrorMessage(err) || t("auth.loginPage.mfa.failed");
  }
}

function onMfaCancel() {
  mfaChallenge.value = null;
  mfaCode.value = "";
  password.value = "";
  error.value = null;
}

onMounted(() => {
  if (authStore.isAuthenticated) {
    void router.replace("/");
  }
});
</script>

<template>
  <form v-if="!mfaChallenge" class="login-view" @submit.prevent="onSubmit">
    <AppPageTitle :level="2" class="login-view__title" icon="right-to-bracket">
      {{ t("auth.loginPage.title") }}
    </AppPageTitle>

    <AppInput
      v-model="username"
      type="text"
      :label="t('auth.loginPage.usernameOrEmail')"
      :required="true"
      :disabled="isLoading"
    />

    <AppInput
      v-model="password"
      type="password"
      :label="t('auth.loginPage.password')"
      :required="true"
      :disabled="isLoading"
    />

    <p v-if="error" class="login-view__error" role="alert" aria-live="polite">
      {{ error }}
    </p>

    <AppButton
      type="submit"
      :loading="isLoading"
      class="login-view__submit"
      icon="right-to-bracket"
    >
      {{ t("auth.loginPage.submit") }}
    </AppButton>

    <nav class="login-view__links">
      <RouterLink to="/password-reset">
        {{ t("auth.loginPage.forgotPassword") }}
      </RouterLink>
      <RouterLink v-if="showRegisterLink" to="/register">
        {{ t("auth.loginPage.noAccount") }}
      </RouterLink>
    </nav>
  </form>

  <form v-else class="login-view" @submit.prevent="onMfaCodeSubmit">
    <AppPageTitle :level="2" class="login-view__title" icon="shield-halved">
      {{ t("auth.loginPage.mfa.title") }}
    </AppPageTitle>

    <p class="login-view__mfa-hint">
      {{ t("auth.loginPage.mfa.hint") }}
    </p>

    <AppInput
      v-if="supportsCode"
      v-model="mfaCode"
      type="text"
      :label="t('auth.loginPage.mfa.code')"
      :required="true"
      :disabled="isLoading"
      autocomplete="one-time-code"
    />

    <p v-if="error" class="login-view__error" role="alert" aria-live="polite">
      {{ error }}
    </p>

    <AppButton
      v-if="supportsCode"
      type="submit"
      :loading="isLoading"
      class="login-view__submit"
      icon="check"
    >
      {{ t("auth.loginPage.mfa.submit") }}
    </AppButton>

    <AppButton
      v-if="supportsSecurityKey"
      type="button"
      :disabled="isLoading"
      class="login-view__submit"
      icon="key"
      @click="onMfaSecurityKey"
    >
      {{ t("auth.loginPage.mfa.useSecurityKey") }}
    </AppButton>

    <nav class="login-view__links">
      <a href="#" @click.prevent="onMfaCancel">
        {{ t("auth.loginPage.mfa.back") }}
      </a>
    </nav>
  </form>
</template>

<style scoped>
.login-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.login-view__title {
  margin: 0 0 var(--space-2);
  font-size: 1.5rem;
  text-align: center;
}

.login-view__mfa-hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-secondary, var(--color-text-muted, inherit));
}

.login-view__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.login-view__submit {
  width: 100%;
}

.login-view__links {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: 0.875rem;
  text-align: center;
}

.login-view__links a {
  color: var(--color-text-link);
}
</style>
