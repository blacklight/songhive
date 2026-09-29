<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  connectDisconnect,
  connectOnboard,
  connectStatus,
  type ConnectStatus,
} from "@/api/payments";
import { getApiErrorMessage } from "@/api/client";
import { useInstanceStore } from "@/stores/instance";
import { useConfirmStore } from "@/stores/confirm";
import { CONNECT_COUNTRIES } from "@/utils/countries";
import AppButton from "@/components/ui/AppButton.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import MembershipPanel from "@/components/payments/MembershipPanel.vue";

const { t } = useI18n();
const instanceStore = useInstanceStore();
const confirm = useConfirmStore();

const connect = ref<ConnectStatus | null>(null);
const connectLoading = ref(true);
const busy = ref(false);
const connectError = ref<string | null>(null);
const country = ref("");

const countryOptions = computed(() => [
  { value: "", label: t("payments.connect.countryPlaceholder") },
  ...CONNECT_COUNTRIES.map((c) => ({ value: c.code, label: c.name })),
]);

const connectStateLabel = computed(() => {
  if (!connect.value?.connected) return t("payments.connect.state.none");
  if (connect.value.onboarded) return t("payments.connect.state.ready");
  return t("payments.connect.state.pending");
});

async function loadConnect() {
  connectLoading.value = true;
  try {
    connect.value = await connectStatus();
  } catch {
    connect.value = null;
  } finally {
    connectLoading.value = false;
  }
}

async function startOnboarding() {
  if (!connect.value?.connected && !country.value) return;
  busy.value = true;
  connectError.value = null;
  try {
    const { onboarding_url } = await connectOnboard({
      refresh_path: "/settings?tab=billing",
      return_path: "/settings?tab=billing",
      country: country.value || undefined,
    });
    window.location.href = onboarding_url;
  } catch (err) {
    connectError.value = getApiErrorMessage(
      err,
      t("payments.connect.onboardError"),
    );
    busy.value = false;
  }
}

async function disconnect() {
  const ok = await confirm.open({
    title: t("payments.connect.disconnectTitle"),
    message: t("payments.connect.disconnectMessage"),
  });
  if (!ok) return;
  busy.value = true;
  connectError.value = null;
  try {
    await connectDisconnect();
    await loadConnect();
  } catch (err) {
    connectError.value = getApiErrorMessage(
      err,
      t("payments.connect.disconnectError"),
    );
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  if (instanceStore.paymentsEnabled) void loadConnect();
  else connectLoading.value = false;
});
</script>

<template>
  <div class="billing-tab">
    <section v-if="instanceStore.paymentsEnabled" class="billing-tab__section">
      <h3>{{ t("payments.membership.title") }}</h3>
      <MembershipPanel />
    </section>

    <section v-if="instanceStore.paymentsEnabled" class="billing-tab__section">
      <h3>{{ t("payments.connect.title") }}</h3>
      <AppSpinner v-if="connectLoading" size="sm" />
      <template v-else>
        <p class="billing-tab__note">{{ t("payments.connect.hint") }}</p>
        <p>
          <strong>{{ t("payments.connect.statusLabel") }}:</strong>
          {{ connectStateLabel }}
        </p>
        <p v-if="connectError" class="billing-tab__error" role="alert">
          {{ connectError }}
        </p>
        <div v-if="!connect?.connected" class="billing-tab__country">
          <AppSelect
            v-model="country"
            :options="countryOptions"
            :label="t('payments.connect.countryLabel')"
            :disabled="busy"
          />
        </div>
        <div class="billing-tab__actions">
          <AppButton
            v-if="!connect?.onboarded"
            icon="building-columns"
            :loading="busy"
            :disabled="!connect?.connected && !country"
            @click="startOnboarding"
          >
            {{
              connect?.connected
                ? t("payments.connect.resume")
                : t("payments.connect.start")
            }}
          </AppButton>
          <AppButton
            v-if="connect?.connected"
            variant="danger"
            icon="link-slash"
            :loading="busy"
            @click="disconnect"
          >
            {{ t("payments.connect.disconnect") }}
          </AppButton>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.billing-tab__section {
  margin-bottom: var(--space-6, 2rem);
}

.billing-tab__section h3 {
  margin-top: 0;
}

.billing-tab__note {
  color: var(--color-text-secondary, #666);
}

.billing-tab__error {
  color: var(--color-danger, #c00);
}

.billing-tab__country {
  max-width: 20rem;
  margin-bottom: var(--space-3, 0.75rem);
}

.billing-tab__actions {
  display: flex;
  gap: var(--space-3, 0.75rem);
  flex-wrap: wrap;
}
</style>
