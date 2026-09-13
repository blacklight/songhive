<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { getActivity, type ActivityResponse } from "@/api/activities";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

// Permalink page for a single activity — the SPA destination of the
// ``{actor}/objects/{id}`` object URLs. Renders the card with its reply
// threads expanded, Mastodon-style, preceded by its ancestor chain so a
// reply opens with its conversation context.
const { t } = useI18n();
const route = useRoute();

const ENTITY_ROUTES: Record<string, string> = {
  track: "tracks",
  album: "albums",
  artist: "artists",
  playlist: "playlists",
  library: "libraries",
};

const MAX_ANCESTORS = 20;

const activity = ref<ActivityResponse | null>(null);
const ancestors = ref<ActivityResponse[]>([]);
const loading = ref(true);
const failed = ref(false);

async function load(activityId: string) {
  loading.value = true;
  failed.value = false;
  activity.value = null;
  ancestors.value = [];
  try {
    const main = await getActivity(activityId);
    activity.value = main;
    const chain: ActivityResponse[] = [];
    const seen = new Set([main.id]);
    let cursor = main.in_reply_to_activity_id ?? null;
    while (cursor && !seen.has(cursor) && chain.length < MAX_ANCESTORS) {
      seen.add(cursor);
      try {
        const parent = await getActivity(cursor);
        chain.unshift(parent);
        cursor = parent.in_reply_to_activity_id ?? null;
      } catch {
        // A deleted or unviewable parent ends the chain.
        break;
      }
    }
    ancestors.value = chain;
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.params.id,
  (id) => {
    if (typeof id === "string" && id) void load(id);
  },
  { immediate: true },
);

// ``user`` entities have no activity feed of their own (statuses live on
// the profile), so the back link only exists for content entities.
const backLink = computed(() => {
  const a = activity.value;
  if (!a) return null;
  const plural = ENTITY_ROUTES[a.entity_type];
  if (!plural) return null;
  return {
    to: `/${plural}/${a.entity_id}/activities`,
    label: t("activities.backTo", {
      entity: t(`browse.entities.${a.entity_type}`, a.entity_type),
    }),
  };
});
</script>

<template>
  <div class="activity-view">
    <AppPageTitle icon="comments">{{
      t("activities.detailTitle")
    }}</AppPageTitle>
    <RouterLink v-if="backLink" :to="backLink.to" class="activity-view__back">
      <AppIcon name="arrow-left" spacing="right" />{{ backLink.label }}
    </RouterLink>
    <SkeletonLoader v-if="loading" variant="card" />
    <p
      v-else-if="failed || !activity"
      class="activity-view__error"
      role="alert"
    >
      {{ t("activities.objectUnavailable") }}
    </p>
    <template v-else>
      <div v-if="ancestors.length" class="activity-view__ancestors">
        <ActivityCard
          v-for="ancestor in ancestors"
          :key="ancestor.id"
          :activity="ancestor"
        />
      </div>
      <ActivityCard :key="activity.id" :activity="activity" expand-replies />
    </template>
  </div>
</template>

<style scoped>
.activity-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.activity-view__back {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-secondary);
  text-decoration: none;
}

.activity-view__back:hover {
  text-decoration: underline;
}

.activity-view__ancestors {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-left: var(--space-4);
  border-left: 2px solid var(--color-border);
}

.activity-view__ancestors :deep(.activity-card) {
  width: auto;
}

.activity-view__error {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}
</style>
