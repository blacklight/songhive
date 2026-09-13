<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { ActivityResponse } from "@/api/activities";
import { fetchActivityCached } from "@/utils/activityFetch";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import NotificationItemCard from "@/components/notifications/NotificationItemCard.vue";
import ActivityCard from "./ActivityCard.vue";

// Renders the object a ``like``/``announce`` activity points at: the full
// activity card for ``Note`` objects, the compact item card for ``Audio``
// (canonical track shares), a muted placeholder when the object is gone or
// not viewable by the requester.
const props = defineProps<{
  activityId: string;
}>();

const emit = defineEmits<{
  loaded: [activity: ActivityResponse];
  error: [];
}>();

const { t } = useI18n();

const ITEM_PLURALS: Record<string, string> = {
  track: "tracks",
  album: "albums",
  artist: "artists",
  playlist: "playlists",
  library: "libraries",
  radio: "radios",
  file: "files",
};

const activity = ref<ActivityResponse | null>(null);
const loading = ref(true);

const isAudio = computed(() => activity.value?.object_type === "Audio");
const itemTo = computed(() => {
  const a = activity.value;
  if (!a) return "";
  return `/${ITEM_PLURALS[a.entity_type] ?? `${a.entity_type}s`}/${a.entity_id}`;
});

onMounted(async () => {
  activity.value = await fetchActivityCached(props.activityId);
  loading.value = false;
  if (activity.value) emit("loaded", activity.value);
  else emit("error");
});
</script>

<template>
  <div v-if="loading" class="activity-embed__loading">
    <AppSpinner />
  </div>
  <p v-else-if="!activity" class="activity-embed__unavailable">
    {{ t("activities.objectUnavailable") }}
  </p>
  <NotificationItemCard
    v-else-if="isAudio"
    :item-type="activity.entity_type"
    :item-id="activity.entity_id"
    :to="itemTo"
  />
  <ActivityCard v-else :activity="activity" class="activity-embed__card" />
</template>

<style scoped>
.activity-embed__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-4);
}

.activity-embed__unavailable {
  margin: 0;
  color: var(--color-text-muted);
}

.activity-embed__card {
  width: 100%;
}
</style>
