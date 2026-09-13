<script setup lang="ts">
import { onMounted, ref } from "vue";
import { type ActivityResponse } from "@/api/activities";
import { fetchActivityCached } from "@/utils/activityFetch";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";

const props = defineProps<{
  activityId: string;
}>();

const emit = defineEmits<{
  error: [];
}>();

const activity = ref<ActivityResponse | null>(null);
const loading = ref(true);

onMounted(async () => {
  activity.value = await fetchActivityCached(props.activityId);
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
