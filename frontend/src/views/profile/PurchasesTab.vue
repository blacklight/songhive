<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { buildUrl } from "@/api/config";
import { listMyOrders, type Order } from "@/api/payments";
import { getApiErrorMessage } from "@/api/client";
import { formatPrice } from "@/utils/money";
import { formatDateTime } from "@/i18n";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();

const orders = ref<Order[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

function archiveUrl(order: Order): string {
  return buildUrl(`/api/v1/payments/orders/${order.id}/archive`);
}

function statusLabel(order: Order): string {
  return t(`payments.purchases.status.${order.status}`);
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    orders.value = await listMyOrders();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("payments.purchases.loadError"));
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="purchases-tab">
    <AppSpinner v-if="loading" />
    <template v-else>
      <p v-if="error" class="purchases-tab__error" role="alert">{{ error }}</p>
      <p v-else-if="orders.length === 0" class="purchases-tab__empty">
        {{ t("payments.purchases.empty") }}
      </p>
      <ul v-else class="purchases-tab__list">
        <li
          v-for="order in orders"
          :key="order.id"
          class="purchases-tab__order"
        >
          <header class="purchases-tab__order-head">
            <span>{{ formatPrice(order.total_minor, order.currency) }}</span>
            <span class="purchases-tab__status">{{ statusLabel(order) }}</span>
            <time v-if="order.created_at">
              {{ formatDateTime(order.created_at) }}
            </time>
          </header>
          <ul v-if="order.tracks.length" class="purchases-tab__tracks">
            <li v-for="track in order.tracks" :key="track.track_id">
              <RouterLink
                :to="{ name: 'track', params: { id: track.track_id } }"
              >
                {{ track.title ?? track.track_id }}
              </RouterLink>
            </li>
          </ul>
          <a
            v-if="order.status === 'paid'"
            class="purchases-tab__archive"
            :href="archiveUrl(order)"
          >
            {{ t("payments.purchases.downloadArchive") }}
          </a>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.purchases-tab__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 1rem);
}

.purchases-tab__order {
  border: 1px solid var(--color-border, #ddd);
  border-radius: var(--radius-md, 8px);
  padding: var(--space-4, 1rem);
}

.purchases-tab__order-head {
  display: flex;
  gap: var(--space-4, 1rem);
  align-items: baseline;
  font-weight: 600;
}

.purchases-tab__status {
  text-transform: capitalize;
}

.purchases-tab__tracks {
  margin: var(--space-2, 0.5rem) 0;
  padding-inline-start: var(--space-5, 1.25rem);
}

.purchases-tab__error {
  color: var(--color-danger, #c00);
}

.purchases-tab__empty {
  color: var(--color-text-secondary, #666);
}
</style>
