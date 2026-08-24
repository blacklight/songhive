<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import { ApiError } from "@/api/client";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

const username = ref("");
const password = ref("");
const error = ref<string | null>(null);
const isLoading = computed(() => authStore.status === "loading");

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

async function onSubmit() {
  error.value = null;
  if (!username.value || !password.value) return;

  try {
    await authStore.login(username.value, password.value);
    const target = safeRedirect(route.query.redirect);
    await router.replace(target);
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      error.value = t("auth.loginPage.emailNotVerified");
    } else {
      error.value = t("auth.loginPage.failed");
    }
  }
}

onMounted(() => {
  if (authStore.isAuthenticated) {
    void router.replace("/");
  }
});
</script>

<template>
  <form class="login-view" @submit.prevent="onSubmit">
    <h2 class="login-view__title">{{ t("auth.loginPage.title") }}</h2>

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

    <p
      v-if="error"
      class="login-view__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <AppButton
      type="submit"
      :loading="isLoading"
      class="login-view__submit"
    >
      {{ t("auth.loginPage.submit") }}
    </AppButton>

    <nav class="login-view__links">
      <RouterLink to="/password-reset">
        {{ t("auth.loginPage.forgotPassword") }}
      </RouterLink>
      <RouterLink to="/register">
        {{ t("auth.loginPage.noAccount") }}
      </RouterLink>
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
  color: var(--color-accent-contrast);
}
</style>
