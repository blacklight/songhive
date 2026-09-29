<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  connectOnboard,
  connectStatus,
  createSale,
  deleteSale,
  saleOffer,
  updateSale,
  type Sale,
  type SaleEntityType,
  type UnpaidPolicy,
} from "@/api/payments";
import { getApiErrorMessage } from "@/api/client";
import { formatPrice, toMajorUnits, toMinorUnits } from "@/utils/money";
import { CONNECT_COUNTRIES } from "@/utils/countries";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const props = defineProps<{
  entityType: SaleEntityType;
  entityId: string;
  /** Track duration in seconds, for bounding sample length. */
  durationSeconds?: number | null;
}>();

const { t } = useI18n();
const confirm = useConfirmStore();
const toast = useToastStore();

const sale = ref<Sale | null>(null);
const connected = ref<boolean | null>(null);
const loading = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);

const price = ref("");
const currency = ref("USD");
const unpaidPolicy = ref<UnpaidPolicy>("sample");
const sampleStart = ref(0);
const sampleLength = ref<number | "">(30);
const country = ref("");

const countryOptions = computed(() => [
  { value: "", label: t("payments.connect.countryPlaceholder") },
  ...CONNECT_COUNTRIES.map((c) => ({ value: c.code, label: c.name })),
]);

const policyOptions = computed(() => [
  { value: "full_stream", label: t("payments.sale.policy.fullStream") },
  { value: "sample", label: t("payments.sale.policy.sample") },
  { value: "none", label: t("payments.sale.policy.none") },
]);

const showSampleFields = computed(() => unpaidPolicy.value === "sample");

const sampleBoundsError = computed(() => {
  if (!showSampleFields.value) return null;
  if (sampleStart.value < 0) return t("payments.sale.sampleBoundsError");
  if (props.durationSeconds != null) {
    const end = sampleStart.value + (sampleLength.value || 0);
    if (end > props.durationSeconds) {
      return t("payments.sale.sampleBoundsError");
    }
  }
  return null;
});

function applySale(s: Sale) {
  sale.value = s;
  price.value = toMajorUnits(s.price_minor, s.currency);
  currency.value = s.currency;
  unpaidPolicy.value = s.unpaid_policy;
  sampleStart.value = s.sample_start_seconds;
  sampleLength.value = s.sample_length_seconds ?? "";
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [status, offer] = await Promise.all([
      connectStatus().catch(() => null),
      saleOffer(props.entityType, props.entityId).catch(() => null),
    ]);
    connected.value = status?.connected ?? false;
    if (offer) applySale(offer);
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.sale.loadError"));
  } finally {
    loading.value = false;
  }
}

async function onboard() {
  if (!country.value) return;
  saving.value = true;
  error.value = null;
  try {
    const { onboarding_url } = await connectOnboard({
      refresh_path: window.location.pathname,
      return_path: window.location.pathname,
      country: country.value,
    });
    window.location.href = onboarding_url;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.sale.onboardError"));
    saving.value = false;
  }
}

async function save(publish: boolean) {
  const minor = toMinorUnits(price.value, currency.value);
  if (minor === null) {
    error.value = t("payments.sale.priceInvalid");
    return;
  }
  if (sampleBoundsError.value) {
    error.value = sampleBoundsError.value;
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    const body = {
      price_minor: minor,
      currency: currency.value,
      unpaid_policy: unpaidPolicy.value,
      sample_start_seconds: sampleStart.value,
      sample_length_seconds:
        showSampleFields.value && sampleLength.value !== ""
          ? sampleLength.value
          : null,
      publish,
    };
    const result = sale.value
      ? await updateSale(sale.value.id, body)
      : await createSale({
          entity_type: props.entityType,
          entity_id: props.entityId,
          ...body,
        });
    applySale(result);
    toast.push({
      type: "success",
      message: publish
        ? t("payments.sale.published")
        : t("payments.sale.saved"),
    });
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.sale.saveError"));
  } finally {
    saving.value = false;
  }
}

async function deactivate() {
  if (!sale.value) return;
  const ok = await confirm.open({
    title: t("payments.sale.deactivateTitle"),
    message: t("payments.sale.deactivateMessage"),
  });
  if (!ok) return;
  saving.value = true;
  try {
    await deleteSale(sale.value.id);
    sale.value = { ...sale.value, status: "inactive" };
    toast.push({ type: "success", message: t("payments.sale.deactivated") });
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.sale.saveError"));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
watch(() => [props.entityType, props.entityId], load);
</script>

<template>
  <section class="sale-editor">
    <h3 class="sale-editor__heading">{{ t("payments.sale.title") }}</h3>

    <AppSpinner v-if="loading" size="sm" />
    <template v-else>
      <p v-if="error" class="sale-editor__error" role="alert">{{ error }}</p>

      <div v-if="connected === false" class="sale-editor__connect">
        <p>{{ t("payments.sale.connectRequired") }}</p>
        <div class="sale-editor__country">
          <AppSelect
            v-model="country"
            :options="countryOptions"
            :label="t('payments.connect.countryLabel')"
            :disabled="saving"
          />
        </div>
        <AppButton
          icon="plug"
          :loading="saving"
          :disabled="!country"
          @click="onboard"
        >
          {{ t("payments.sale.connect") }}
        </AppButton>
      </div>

      <template v-else>
        <p v-if="sale" class="sale-editor__status">
          {{ t("payments.sale.statusLabel") }}:
          {{ t(`payments.sale.status.${sale.status}`) }}
          <template v-if="sale.status === 'active'">
            — {{ formatPrice(sale.price_minor, sale.currency) }}
          </template>
        </p>

        <div class="sale-editor__grid">
          <AppInput
            v-model="price"
            type="text"
            inputmode="decimal"
            :label="t('payments.sale.price')"
            :disabled="saving"
          />
          <AppInput
            v-model="currency"
            type="text"
            maxlength="3"
            :label="t('payments.sale.currency')"
            :disabled="saving"
          />
          <AppSelect
            v-model="unpaidPolicy"
            :options="policyOptions"
            :label="t('payments.sale.unpaidPolicy')"
            :disabled="saving"
          />
        </div>

        <div v-if="showSampleFields" class="sale-editor__grid">
          <AppInput
            v-model.number="sampleStart"
            type="number"
            min="0"
            :label="t('payments.sale.sampleStart')"
            :disabled="saving"
          />
          <AppInput
            v-model.number="sampleLength"
            type="number"
            min="1"
            :label="t('payments.sale.sampleLength')"
            :disabled="saving"
          />
        </div>
        <p v-if="sampleBoundsError" class="sale-editor__error">
          {{ sampleBoundsError }}
        </p>

        <div class="sale-editor__actions">
          <AppButton
            icon="check"
            :loading="saving"
            :disabled="!!sampleBoundsError"
            @click="save(true)"
          >
            {{
              sale?.status === "active"
                ? t("payments.sale.updatePublished")
                : t("payments.sale.publish")
            }}
          </AppButton>
          <AppButton
            variant="secondary"
            icon="floppy-disk"
            :loading="saving"
            @click="save(false)"
          >
            {{ t("payments.sale.saveDraft") }}
          </AppButton>
          <AppButton
            v-if="sale && sale.status === 'active'"
            variant="danger"
            icon="circle-stop"
            :loading="saving"
            @click="deactivate"
          >
            {{ t("payments.sale.deactivate") }}
          </AppButton>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.sale-editor {
  border: 1px solid var(--color-border, #ddd);
  border-radius: var(--radius-md, 8px);
  padding: var(--space-4, 1rem);
  margin-top: var(--space-4, 1rem);
}

.sale-editor__heading {
  margin-top: 0;
}

.sale-editor__connect {
  display: flex;
  flex-direction: column;
  gap: var(--space-3, 0.75rem);
}

.sale-editor__error {
  color: var(--color-danger, #c00);
}

.sale-editor__status {
  color: var(--color-text-secondary, #666);
}

.sale-editor__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: var(--space-3, 0.75rem);
}

.sale-editor__actions {
  display: flex;
  gap: var(--space-3, 0.75rem);
  flex-wrap: wrap;
  margin-top: var(--space-4, 1rem);
}
</style>
