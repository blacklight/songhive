<script setup lang="ts">
import { onMounted, ref } from "vue";
import { getActivity, type ActivityResponse } from "@/api/activities";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";

const props = defineProps<{
  activityId: string;
}>();

const emit = defineEmits<{
  error: [];
}>();

// Notification rows referencing the same activity share one fetch; results
// are cached for the lifetime of the page so re-renders are free.
const cache = new Map<string, Promise<ActivityResponse | null>>();

function fetchActivity(activityId: string): Promise<ActivityResponse | null> {
  let pending = cache.get(activityId);
  if (!pending) {
    pending = getActivity(activityId).catch(() => null);
    cache.set(activityId, pending);
  }
  return pending;
}

const activity = ref<ActivityResponse | null>(null);
const loading = ref(true);

onMounted(async () => {
  activity.value = await fetchActivity(props.activityId);
  loading.value = false;
  if (activity.value === null) emit("error");
});
</script>

<template>
  <ActivityCard v-if="activity" :activity="activity" />
  <div v-else-if="loading" class="notification-activity__loading">
    <AppSpinner />
  </div>
</template>

<style scoped>
.notification-activity__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-4);
}
</style>
