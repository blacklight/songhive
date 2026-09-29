<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import MembershipPanel from "@/components/payments/MembershipPanel.vue";

const { t } = useI18n();
const route = useRoute();

// The billing capability arrives as ?bcap=<token> (verification email or the
// 402 login response); it authorizes only these membership endpoints.
const billingToken = computed(() => {
  const raw = route.query.bcap ?? route.query.billing_token;
  return typeof raw === "string" ? raw : undefined;
});

// The provider success redirect also carries ?order=<checkout_token> — an
// opaque credential authorizing membership-status reads for the buyer.
const orderToken = computed(() => {
  const raw = route.query.order;
  return typeof raw === "string" ? raw : undefined;
});

// /billing/success and /billing/cancel are the provider redirect targets.
const outcome = computed(() =>
  route.path.endsWith("/success")
    ? "success"
    : route.path.endsWith("/cancel")
      ? "cancel"
      : null,
);

// Set by the panel once the webhook-confirmed status lands — until then the
// notice must not imply the payment went through. The value selects which
// follow-up message fits: the buyer was signed in, must verify their email,
// or can log in.
const confirmed = ref<"signed_in" | "verify" | "login" | null>(null);
</script>

<template>
  <main class="billing-view">
    <AppPageTitle class="billing-view__title" icon="id-card">
      {{ t("payments.membership.title") }}
    </AppPageTitle>

    <p v-if="outcome === 'success'" class="billing-view__notice">
      {{
        confirmed
          ? t(`payments.membership.checkoutConfirmed.${confirmed}`)
          : t("payments.membership.checkoutSuccess")
      }}
    </p>
    <p
      v-else-if="outcome === 'cancel'"
      class="billing-view__notice billing-view__notice--muted"
    >
      {{ t("payments.membership.checkoutCancel") }}
    </p>

    <MembershipPanel
      :billing-token="billingToken"
      :order-token="orderToken"
      :checkout-return="outcome === 'success'"
      @confirmed="confirmed = $event"
    />
  </main>
</template>

<style scoped>
.billing-view {
  max-width: 36rem;
  margin: 0 auto;
  padding: var(--space-6, 2rem) var(--space-4, 1rem);
}

.billing-view__notice {
  padding: var(--space-3, 0.75rem) var(--space-4, 1rem);
  border-radius: var(--radius-md, 0.5rem);
  background: var(--surface-alt, rgba(0, 0, 0, 0.04));
  margin-bottom: var(--space-4, 1rem);
}

.billing-view__notice--muted {
  opacity: 0.75;
}
</style>
