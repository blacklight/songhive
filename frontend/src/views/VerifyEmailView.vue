<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { verifyEmail } from "@/api/auth";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppBanner from "@/components/feedback/AppBanner.vue";

const { t } = useI18n();
const route = useRoute();

const rawToken = Array.isArray(route.query.token)
  ? route.query.token[0]
  : route.query.token;
const token = rawToken || "";

const isLoading = ref(!!token);
const isSuccess = ref<boolean | null>(null);

onMounted(async () => {
  if (!token) {
    isSuccess.value = false;
    return;
  }

  try {
    await verifyEmail({ token });
    isSuccess.value = true;
  } catch {
    isSuccess.value = false;
  } finally {
    isLoading.value = false;
  }
});
</script>

<template>
  <div class="verify-email-view">
    <h2 class="verify-email-view__title">{{ t("auth.verifyEmail.title") }}</h2>

    <div v-if="isLoading" class="verify-email-view__status">
      <AppSpinner />
      <p>{{ t("auth.verifyEmail.verifying") }}</p>
    </div>

    <template v-else-if="isSuccess === true">
      <AppBanner type="success" :title="t('auth.verifyEmail.title')">
        {{ t("auth.verifyEmail.success") }}
      </AppBanner>
      <RouterLink to="/login" class="verify-email-view__link">
        {{ t("auth.verifyEmail.backToLogin") }}
      </RouterLink>
    </template>

    <template v-else-if="isSuccess === false">
      <AppBanner type="error" :title="t('auth.verifyEmail.title')">
        {{ t("auth.verifyEmail.invalidToken") }}
      </AppBanner>
      <RouterLink to="/login" class="verify-email-view__link">
        {{ t("auth.verifyEmail.backToLogin") }}
      </RouterLink>
    </template>
  </div>
</template>

<style scoped>
.verify-email-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  align-items: center;
  text-align: center;
}

.verify-email-view__title {
  margin: 0 0 var(--space-2);
  font-size: 1.5rem;
}

.verify-email-view__status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
}

.verify-email-view__status p {
  margin: 0;
  color: var(--color-text-muted);
}

.verify-email-view__link {
  color: var(--color-accent-contrast);
}
</style>
