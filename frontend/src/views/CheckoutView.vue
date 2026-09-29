<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { listMyOrders, membershipStatus, type Order } from "@/api/payments";
import { useAuthStore } from "@/stores/auth";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();

// Stripe redirects to /checkout/success?order=<checkout_token> or
// /checkout/cancel?order=<checkout_token>. The token authorizes order-status
// polling only — entitlements arrive via the verified webhook, never here.
const outcome = computed(() =>
  route.path.endsWith("/success")
    ? "success"
    : route.path.endsWith("/cancel")
      ? "cancel"
      : "unknown",
);

const order = ref<Order | null>(null);
const membership = ref<string | null>(null);
const loading = ref(false);
const pollAttempts = ref(0);
const MAX_POLLS = 10;
const POLL_MS = 2500;

async function pollRegisteredBuyer() {
  // For signed-in buyers the newest order appears in /orders/mine once the
  // webhook has fulfilled it; poll briefly and show whatever state lands.
  loading.value = true;
  try {
    while (pollAttempts.value < MAX_POLLS) {
      const orders = await listMyOrders().catch(() => null);
      if (orders?.length) {
        const latest = orders[0];
        if (latest.status === "paid") {
          order.value = latest;
          return;
        }
        if (latest.status === "pending" && pollAttempts.value < MAX_POLLS) {
          pollAttempts.value += 1;
          await new Promise((r) => setTimeout(r, POLL_MS));
          continue;
        }
        order.value = latest;
        return;
      }
      // Membership purchases land on the subscription, not the orders list.
      const sub = await membershipStatus().catch(() => null);
      if (sub && sub.status !== "none") {
        membership.value = sub.status;
        if (sub.status === "active") return;
      }
      pollAttempts.value += 1;
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  if (outcome.value === "success" && authStore.isAuthenticated) {
    void pollRegisteredBuyer();
  }
});

function goToPurchases() {
  void router.push({ path: "/settings", query: { tab: "purchases" } });
}
function goToBilling() {
  void router.push({ path: "/settings", query: { tab: "billing" } });
}
function goHome() {
  void router.push("/");
}
</script>

<template>
  <main class="checkout-view">
    <AppPageTitle class="checkout-view__title" icon="credit-card">
      {{
        outcome === "success"
          ? t("payments.checkout.successTitle")
          : outcome === "cancel"
            ? t("payments.checkout.cancelTitle")
            : t("payments.checkout.title")
      }}
    </AppPageTitle>

    <div v-if="outcome === 'success'" class="checkout-view__body">
      <p v-if="order?.status === 'paid' || membership === 'active'">
        {{ t("payments.checkout.confirmed") }}
      </p>
      <p v-else>{{ t("payments.checkout.successBody") }}</p>
      <AppSpinner v-if="loading" size="sm" />
    </div>
    <div v-else-if="outcome === 'cancel'" class="checkout-view__body">
      <p>{{ t("payments.checkout.cancelBody") }}</p>
    </div>
    <div v-else class="checkout-view__body">
      <p>{{ t("payments.checkout.unknownBody") }}</p>
    </div>

    <div class="checkout-view__actions">
      <AppButton icon="bag-shopping" @click="goToPurchases">
        {{ t("payments.checkout.viewPurchases") }}
      </AppButton>
      <AppButton
        v-if="membership"
        variant="secondary"
        icon="id-card"
        @click="goToBilling"
      >
        {{ t("payments.checkout.viewBilling") }}
      </AppButton>
      <AppButton variant="secondary" icon="house" @click="goHome">
        {{ t("nav.home") }}
      </AppButton>
    </div>
  </main>
</template>

<style scoped>
.checkout-view {
  max-width: 36rem;
  margin: 0 auto;
  padding: var(--space-6, 2rem) var(--space-4, 1rem);
}

.checkout-view__body {
  margin: var(--space-4, 1rem) 0;
  color: var(--color-text-secondary, #666);
}

.checkout-view__actions {
  display: flex;
  gap: var(--space-3, 0.75rem);
  flex-wrap: wrap;
}
</style>
