<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import FeedButton from "@/components/ui/FeedButton.vue";
import ActivityFeed from "@/components/activities/ActivityFeed.vue";
import { activityFeedUrls } from "@/utils/feeds";
import { useFeedLinks } from "@/composables/useFeedLinks";

const props = defineProps<{ entityType: string; entityId: string }>();

const { t } = useI18n();

const ENTITY_ROUTES: Record<string, string> = {
  track: "tracks",
  album: "albums",
  artist: "artists",
  playlist: "playlists",
  library: "libraries",
  radio: "radios",
};

const entityRoute = computed(
  () => `/${ENTITY_ROUTES[props.entityType] ?? ""}/${props.entityId}`,
);
const feedUrls = computed(() =>
  activityFeedUrls(ENTITY_ROUTES[props.entityType] ?? "", props.entityId),
);
useFeedLinks(feedUrls);
const entityLabel = computed(() =>
  t(`browse.entities.${props.entityType}`, props.entityType),
);
</script>

<template>
  <div class="entity-activities-view">
    <AppPageTitle icon="comments">{{ t("activities.title") }}</AppPageTitle>
    <div class="entity-activities-view__header">
      <RouterLink :to="entityRoute" class="entity-activities-view__back">
        <AppIcon name="arrow-left" spacing="right" />{{
          t("activities.backTo", { entity: entityLabel })
        }}
      </RouterLink>
      <FeedButton :urls="feedUrls" />
    </div>
    <ActivityFeed
      :key="`${entityType}:${entityId}`"
      :entity-type="entityType"
      :entity-id="entityId"
    />
  </div>
</template>

<style scoped>
.entity-activities-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.entity-activities-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.entity-activities-view__back {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-secondary);
  text-decoration: none;
}

.entity-activities-view__back:hover {
  text-decoration: underline;
}
</style>
