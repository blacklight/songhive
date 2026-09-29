<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  checkout,
  listMyOrders,
  saleOffer,
  type Sale,
  type SaleEntityType,
} from "@/api/payments";
import { getApiErrorMessage } from "@/api/client";
import { formatPrice } from "@/utils/money";
import { useAuthStore } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const props = defineProps<{
  entityType: SaleEntityType;
  entityId: string;
}>();

const { t } = useI18n();
const authStore = useAuthStore();
const instanceStore = useInstanceStore();

const sale = ref<Sale | null>(null);
const purchased = ref(false);
const loading = ref(true);
const busy = ref(false);
const error = ref<string | null>(null);
const guestEmail = ref("");
const needsEmail = computed(() => !authStore.isAuthenticated);

const policyLabel = computed(() => {
  if (!sale.value) return "";
  return t(`payments.purchase.policy.${sale.value.unpaid_policy}`);
});

async function load() {
  loading.value = true;
  purchased.value = false;
  try {
    sale.value = await saleOffer(props.entityType, props.entityId);
    if (sale.value && authStore.isAuthenticated) {
      const orders = await listMyOrders();
      purchased.value = orders.some(
        (order) => order.sale_id === sale.value!.id && order.status === "paid",
      );
    }
  } catch {
    sale.value = null;
  } finally {
    loading.value = false;
  }
}

async function buy() {
  if (needsEmail.value && !guestEmail.value.trim()) {
    error.value = t("payments.purchase.emailRequired");
    return;
  }
  busy.value = true;
  error.value = null;
  try {
    const { checkout_url } = await checkout({
      item_type: props.entityType,
      item_id: props.entityId,
      guest_email: needsEmail.value ? guestEmail.value.trim() : undefined,
    });
    window.location.href = checkout_url;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.purchase.checkoutError"));
    busy.value = false;
  }
}

onMounted(() => {
  if (instanceStore.paymentsEnabled) void load();
  else loading.value = false;
});
watch(() => [props.entityType, props.entityId], load);
</script>

<template>
  <AppSpinner v-if="loading" size="sm" />
  <p v-else-if="sale && purchased" class="purchase-panel__owned">
    <AppIcon name="circle-check" spacing="right" />
    {{ t("payments.purchase.owned") }}
  </p>
  <section v-else-if="sale" class="purchase-panel">
    <p class="purchase-panel__price">
      {{ formatPrice(sale.price_minor, sale.currency) }}
    </p>
    <p class="purchase-panel__policy">{{ policyLabel }}</p>

    <AppInput
      v-if="needsEmail"
      v-model="guestEmail"
      type="email"
      :label="t('payments.purchase.email')"
      :hint="t('payments.purchase.emailHint')"
      :disabled="busy"
    />

    <p v-if="error" class="purchase-panel__error" role="alert">{{ error }}</p>

    <AppButton icon="bag-shopping" :loading="busy" @click="buy">
      {{ t("payments.purchase.buy") }}
    </AppButton>
  </section>
</template>

<style scoped>
.purchase-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3, 0.75rem);
  padding: var(--space-4, 1rem);
  border: 1px solid var(--color-border, #ddd);
  border-radius: var(--radius-md, 8px);
}

.purchase-panel__price {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0;
}

.purchase-panel__policy {
  margin: 0;
  color: var(--color-text-secondary, #666);
}

.purchase-panel__error {
  color: var(--color-danger, #c00);
  margin: 0;
}
</style>
