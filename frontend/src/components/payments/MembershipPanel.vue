<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  membershipCancel,
  membershipCheckout,
  membershipPortal,
  membershipQuote,
  membershipStatus,
  mintMembershipSession,
  type MembershipQuote,
  type MembershipStatus,
} from "@/api/payments";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { formatPrice } from "@/utils/money";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const props = defineProps<{
  /** Scoped billing capability for users whose account is unpaid-inactive. */
  billingToken?: string;
  /**
   * Opaque checkout token from the provider success redirect (?order=);
   * authorizes membership-status reads for the buyer.
   */
  orderToken?: string;
  /**
   * Set when landing back from provider checkout: poll the subscription
   * status briefly so the confirmed state surfaces once the webhook lands.
   */
  checkoutReturn?: boolean;
}>();

const emit = defineEmits<{
  /** Payment confirmed — how the visitor can proceed afterwards. */
  confirmed: [state: "signed_in" | "verify" | "login"];
}>();

const { t } = useI18n();
const authStore = useAuthStore();
const confirm = useConfirmStore();

const quote = ref<MembershipQuote | null>(null);
const subscription = ref<MembershipStatus | null>(null);
const loading = ref(true);
const busy = ref(false);
const error = ref<string | null>(null);

const priceLabel = computed(() => {
  if (!quote.value) return "";
  return t("payments.membership.pricePer", {
    price: formatPrice(quote.value.price_minor, quote.value.currency),
    interval: t(`payments.membership.interval.${quote.value.interval}`),
  });
});

// paid_through is only written by webhook-confirmed payments, so a future
// period end means paid access even if the raw status lags behind (Stripe
// can deliver invoice.paid before checkout.session.completed).
const isActive = computed(() => {
  const s = subscription.value;
  if (!s) return false;
  if (s.status === "active" || s.status === "trialing") return true;
  return !!s.paid_through && new Date(s.paid_through).getTime() > Date.now();
});

// Order-token-only visitors (post-checkout return) can act while the token
// is still fresh — portal/cancel accept it for the same window the session
// mint does. Anonymous visitors with no credential get the login CTA.
const canManage = computed(
  () => authStore.isAuthenticated || !!props.billingToken || !!props.orderToken,
);

// Opening a new checkout needs a session or billing capability — the order
// token only scopes actions on the checkout it came from.
const canCheckout = computed(
  () => authStore.isAuthenticated || !!props.billingToken,
);

const CONFIRM_POLLS = 8;
const CONFIRM_POLL_MS = 2500;
const mintAttempted = ref(false);

// Once the webhook-confirmed status shows the account active, exchange the
// scoped redirect credential for a real session — the success page then
// signs the buyer in instead of dead-ending on a login prompt. Returns
// whether the visitor ends up authenticated.
async function tryMintSession(): Promise<boolean> {
  if (authStore.isAuthenticated) return true;
  if (mintAttempted.value || !(props.billingToken || props.orderToken))
    return false;
  mintAttempted.value = true;
  try {
    await mintMembershipSession(props.billingToken, props.orderToken);
    await authStore.fetchProfile();
    authStore.status = "authenticated";
    return true;
  } catch {
    // Consumed/expired credential or an account that is paid but not yet
    // active (e.g. email verification still pending) — the caller decides
    // which follow-up message to show.
    return false;
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [q, s] = await Promise.all([
      membershipQuote().catch(() => null),
      membershipStatus(props.billingToken, props.orderToken).catch(() => null),
    ]);
    quote.value = q;
    subscription.value = s;

    // Returning from checkout: the provider redirect is not proof of
    // payment — keep polling until the webhook-confirmed status lands.
    let attempts = 0;
    while (
      props.checkoutReturn &&
      attempts < CONFIRM_POLLS &&
      !isActive.value
    ) {
      attempts += 1;
      await new Promise((r) => setTimeout(r, CONFIRM_POLL_MS));
      const refreshed = await membershipStatus(
        props.billingToken,
        props.orderToken,
      ).catch(() => null);
      if (refreshed) subscription.value = refreshed;
    }
    if (isActive.value) {
      const signedIn = await tryMintSession();
      if (signedIn) {
        emit("confirmed", "signed_in");
      } else if (subscription.value?.email_verified === false) {
        // Paid but not yet active: verification is the last gate.
        emit("confirmed", "verify");
      } else {
        emit("confirmed", "login");
      }
    }
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.membership.loadError"));
  } finally {
    loading.value = false;
  }
}

async function startCheckout() {
  busy.value = true;
  error.value = null;
  try {
    const { checkout_url } = await membershipCheckout(props.billingToken);
    window.location.href = checkout_url;
  } catch (err) {
    error.value = getApiErrorMessage(
      err,
      t("payments.membership.checkoutError"),
    );
    busy.value = false;
  }
}

async function openPortal() {
  busy.value = true;
  error.value = null;
  try {
    const { portal_url } = await membershipPortal(
      props.billingToken,
      props.orderToken,
    );
    window.location.href = portal_url;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.membership.portalError"));
    busy.value = false;
  }
}

async function cancel() {
  const confirmed = await confirm.open({
    title: t("payments.membership.unsubscribe"),
    message: t("payments.membership.cancelConfirm"),
    danger: true,
    confirmLabel: t("common.yes"),
    cancelLabel: t("common.no"),
  });
  if (!confirmed) return;

  busy.value = true;
  error.value = null;
  try {
    subscription.value = await membershipCancel(
      false,
      props.billingToken,
      props.orderToken,
    );
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.membership.cancelError"));
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="membership-panel">
    <AppSpinner v-if="loading" />
    <template v-else>
      <p v-if="error" class="membership-panel__error" role="alert">
        {{ error }}
      </p>

      <section v-if="subscription" class="membership-panel__status">
        <p>
          <strong>{{ t("payments.membership.statusLabel") }}:</strong>
          {{ t(`payments.membership.status.${subscription.status}`) }}
        </p>
        <p v-if="subscription.paid_through">
          {{ t("payments.membership.paidThrough") }}:
          {{ new Date(subscription.paid_through).toLocaleDateString() }}
        </p>
        <p
          v-if="subscription.cancel_at_period_end"
          class="membership-panel__note"
        >
          {{ t("payments.membership.cancelScheduled") }}
        </p>
      </section>

      <p v-if="quote && !isActive" class="membership-panel__price">
        {{ priceLabel }}
      </p>

      <div v-if="canManage" class="membership-panel__actions">
        <AppButton
          v-if="!isActive && canCheckout"
          icon="credit-card"
          :loading="busy"
          @click="startCheckout"
        >
          {{ t("payments.membership.subscribe") }}
        </AppButton>
        <AppButton
          v-if="isActive"
          variant="secondary"
          icon="arrow-up-right-from-square"
          :loading="busy"
          @click="openPortal"
        >
          {{ t("payments.membership.managePortal") }}
        </AppButton>
        <AppButton
          v-if="isActive && !subscription?.cancel_at_period_end"
          variant="danger"
          icon="xmark"
          :loading="busy"
          @click="cancel"
        >
          {{ t("payments.membership.cancel") }}
        </AppButton>
      </div>
      <div v-else-if="subscription" class="membership-panel__actions">
        <AppButton
          icon="right-to-bracket"
          @click="$router.push({ path: '/login' })"
        >
          {{ t("payments.membership.continueLogin") }}
        </AppButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.membership-panel__error {
  color: var(--color-danger, #c00);
}

.membership-panel__note {
  color: var(--color-text-secondary, #666);
}

.membership-panel__price {
  font-size: 1.25rem;
  font-weight: 600;
  margin: var(--space-4, 1rem) 0;
}

.membership-panel__actions {
  display: flex;
  gap: var(--space-3, 0.75rem);
  flex-wrap: wrap;
}
</style>
