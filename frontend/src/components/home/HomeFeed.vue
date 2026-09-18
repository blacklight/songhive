<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { useTimelineStore } from "@/stores/timeline";
import type { TimelineMode, TimelineScope } from "@/api/timeline";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppTabs, { type Tab } from "@/components/ui/AppTabs.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

/**
 * The home-page activity feed backed by ``GET /api/v1/timeline``.
 *
 * Authenticated users get the ``Mine | This instance | Federated`` scope
 * switch — anonymous visitors the ``This instance | Federated`` subset —
 * plus the ``Posts | All activity`` mode switch on a second row.
 */
export interface Props {
  authenticated: boolean;
}

const props = defineProps<Props>();
const { t } = useI18n();
const store = useTimelineStore();

const scopeTabs = computed<Tab[]>(() => [
  ...(props.authenticated
    ? [{ value: "mine", label: t("pages.home.feed.scopes.mine") }]
    : []),
  { value: "instance", label: t("pages.home.feed.scopes.instance") },
  { value: "federated", label: t("pages.home.feed.scopes.federated") },
]);

const modeTabs = computed<Tab[]>(() => [
  { value: "posts", label: t("pages.home.feed.modes.posts") },
  { value: "all", label: t("pages.home.feed.modes.all") },
]);

const currentScope = computed({
  get: () => store.scope,
  set: (value: string) => void store.setScope(value as TimelineScope),
});

const currentMode = computed({
  get: () => store.mode,
  set: (value: string) => void store.setMode(value as TimelineMode),
});

const title = computed(() =>
  props.authenticated
    ? t("pages.home.feed.title")
    : t("pages.home.feed.latestOnInstance"),
);

function refresh() {
  void store.refresh();
}

onMounted(() => void store.init(props.authenticated));
</script>

<template>
  <section class="home-feed">
    <header class="home-feed__header">
      <h2 class="home-feed__title">{{ title }}</h2>
      <div class="home-feed__controls">
        <AppTabs v-model="currentScope" :tabs="scopeTabs" />
        <AppTabs v-model="currentMode" :tabs="modeTabs" />
      </div>
    </header>

    <div
      v-if="store.loading && !store.items.length"
      class="home-feed__list"
      aria-hidden="true"
    >
      <SkeletonLoader v-for="n in 3" :key="n" variant="card" />
    </div>

    <div v-else-if="store.error" class="home-feed__error" role="alert">
      <span>{{ store.error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="refresh">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <p v-else-if="!store.items.length" class="home-feed__empty">
      {{ t("pages.home.feed.empty") }}
    </p>

    <div v-else class="home-feed__list">
      <ActivityCard
        v-for="activity in store.items"
        :key="activity.id"
        :activity="activity"
      />
      <AppButton
        v-if="store.hasMore"
        variant="secondary"
        class="home-feed__more"
        :loading="store.loadingMore"
        @click="store.loadMore()"
      >
        {{ t("activities.loadMore") }}
      </AppButton>
      <p v-if="store.error" class="home-feed__error" role="alert">
        {{ store.error }}
      </p>
    </div>

    <p v-if="!props.authenticated" class="home-feed__signin">
      <RouterLink to="/login">{{ t("pages.home.feed.signInCta") }}</RouterLink>
    </p>
  </section>
</template>

<style scoped>
.home-feed {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.home-feed__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.home-feed__title {
  margin: 0;
  font-size: 1.125rem;
}

.home-feed__controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.home-feed__list {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  margin: 0 calc(-1 * var(--space-3));
  gap: var(--space-3);
}

@media (min-width: 768px) {
  .home-feed__controls {
    align-items: flex-end;
  }

  .home-feed__list {
    margin: 0 auto;
  }
}

.home-feed__empty {
  margin: 0;
  padding: var(--space-4);
  color: var(--color-text-muted);
  text-align: center;
}

.home-feed__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.home-feed__more {
  align-self: stretch;
}

.home-feed__signin {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.home-feed__signin a {
  color: var(--color-text-link);
}
</style>
