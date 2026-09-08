<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  useActivitiesStore,
  type ActivityFeedFilter,
} from "@/stores/activities";
import AppButton from "@/components/ui/AppButton.vue";
import AppTabs, { type Tab } from "@/components/ui/AppTabs.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ActivityCard from "./ActivityCard.vue";

const props = defineProps<{ entityType: string; entityId: string }>();

const { t } = useI18n();
const store = useActivitiesStore();

const FILTERS: ActivityFeedFilter[] = [
  "all",
  "local",
  "remote",
  "replies",
  "boosts",
  "likes",
];

const tabs = computed<Tab[]>(() =>
  FILTERS.map((value) => ({
    value,
    label: t(`activities.filters.${value}`),
  })),
);

const currentFilter = computed({
  get: () => store.filter,
  set: (value: string) => store.setFilter(value as ActivityFeedFilter),
});

function refresh() {
  void store.load(props.entityType, props.entityId);
}

onMounted(refresh);
watch(() => [props.entityType, props.entityId], refresh);
</script>

<template>
  <div class="activity-feed">
    <AppTabs v-model="currentFilter" :tabs="tabs" />

    <div
      v-if="store.loading && !store.items.length"
      class="activity-feed__list"
    >
      <SkeletonLoader v-for="n in 3" :key="n" variant="card" />
    </div>

    <div v-else-if="store.error" class="activity-feed__error" role="alert">
      <span>{{ store.error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="refresh">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <p v-else-if="!store.items.length" class="activity-feed__empty">
      {{ t("activities.empty") }}
    </p>

    <div v-else class="activity-feed__list">
      <ActivityCard
        v-for="activity in store.items"
        :key="activity.id"
        :activity="activity"
      />
      <AppButton
        v-if="store.hasMore"
        variant="secondary"
        class="activity-feed__more"
        :loading="store.loadingMore"
        @click="store.loadMore()"
      >
        {{ t("activities.loadMore") }}
      </AppButton>
      <p v-if="store.error" class="activity-feed__error" role="alert">
        {{ store.error }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.activity-feed {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.activity-feed__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.activity-feed__empty {
  margin: 0;
  color: var(--color-text-muted);
}

.activity-feed__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.activity-feed__more {
  align-self: stretch;
}
</style>
