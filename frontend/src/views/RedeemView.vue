<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { redeem, type RedeemResponse } from "@/api/payments";
import { getApiErrorMessage } from "@/api/client";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();
const route = useRoute();

const token = ref(
  typeof route.params.token === "string"
    ? route.params.token
    : typeof route.query.token === "string"
      ? route.query.token
      : "",
);
const result = ref<RedeemResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const downloadBase = computed(() =>
  result.value
    ? `/api/v1/payments/download/${result.value.download_token}`
    : "",
);

async function onSubmit() {
  if (!token.value.trim()) return;
  loading.value = true;
  error.value = null;
  try {
    result.value = await redeem(token.value.trim());
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.redeem.failed"));
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  if (token.value) void onSubmit();
});
</script>

<template>
  <main class="redeem-view">
    <AppPageTitle class="redeem-view__title" icon="gift">
      {{ t("payments.redeem.title") }}
    </AppPageTitle>

    <template v-if="!result">
      <p class="redeem-view__hint">{{ t("payments.redeem.hint") }}</p>
      <form class="redeem-view__form" @submit.prevent="onSubmit">
        <AppInput
          v-model="token"
          type="text"
          :label="t('payments.redeem.tokenLabel')"
          :required="true"
          :disabled="loading"
        />
        <p v-if="error" class="redeem-view__error" role="alert">
          {{ error }}
        </p>
        <AppButton type="submit" :loading="loading" icon="key">
          {{ t("payments.redeem.submit") }}
        </AppButton>
      </form>
    </template>

    <template v-else>
      <p class="redeem-view__success">{{ t("payments.redeem.success") }}</p>
      <p class="redeem-view__expiry">
        {{ t("payments.redeem.expiry", { days: result.expires_in_days }) }}
      </p>

      <ul class="redeem-view__tracks">
        <li v-for="item in result.order.tracks" :key="item.track_id">
          <span class="redeem-view__track-title">{{ item.title }}</span>
          <a
            class="redeem-view__download"
            :href="`${downloadBase}/track/${item.track_id}`"
          >
            {{ t("common.download") }}
          </a>
        </li>
      </ul>

      <a
        v-if="result.order.tracks.length > 1"
        class="redeem-view__archive"
        :href="`${downloadBase}/archive`"
      >
        {{ t("payments.redeem.downloadAll") }}
      </a>
    </template>
  </main>
</template>

<style scoped>
.redeem-view {
  max-width: 36rem;
  margin: 0 auto;
  padding: var(--space-6, 2rem) var(--space-4, 1rem);
}

.redeem-view__hint,
.redeem-view__expiry {
  color: var(--color-text-secondary, #666);
}

.redeem-view__error {
  color: var(--color-danger, #c00);
}

.redeem-view__tracks {
  list-style: none;
  margin: var(--space-4, 1rem) 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2, 0.5rem);
}

.redeem-view__tracks li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3, 0.75rem);
}

.redeem-view__archive {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3, 0.75rem);
}
</style>
