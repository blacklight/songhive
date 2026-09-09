<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import {
  deleteTag,
  listTagActivities,
  listTagItems,
  type ListTagActivitiesParams,
  type ListTagItemsParams,
} from "@/api/tags";
import TagDetailView, {
  type ListParams,
} from "@/components/tags/TagDetailView.vue";

const route = useRoute();
const tagName = computed(() => String(route.params.name));
const availableTypes: string[] = [
  "artist",
  "album",
  "track",
  "playlist",
  "library",
  "activity",
];

function loadTagItems(name: string, params: ListParams) {
  return listTagItems(name, params as ListTagItemsParams);
}

function loadTagActivities(
  name: string,
  params: { limit?: number; cursor?: string },
) {
  return listTagActivities(name, params as ListTagActivitiesParams);
}
</script>

<template>
  <TagDetailView
    kind="tag"
    :name="tagName"
    :available-types="availableTypes"
    :load-items="loadTagItems"
    :activity-loader="loadTagActivities"
    :delete-item="deleteTag"
  />
</template>
