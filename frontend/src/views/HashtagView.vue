<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import {
  deleteHashtag,
  listHashtagItems,
  type ListHashtagItemsParams,
  type TaggedItemType,
} from "@/api/hashtags";
import TagDetailView, {
  type ListParams,
} from "@/components/tags/TagDetailView.vue";

const route = useRoute();
const hashtagName = computed(() => String(route.params.name));
const availableTypes: TaggedItemType[] = [
  "artist",
  "album",
  "track",
  "playlist",
  "library",
];

function loadHashtagItems(name: string, params: ListParams) {
  return listHashtagItems(name, params as ListHashtagItemsParams);
}
</script>

<template>
  <TagDetailView
    kind="hashtag"
    :name="hashtagName"
    :available-types="availableTypes"
    :load-items="loadHashtagItems"
    :delete-item="deleteHashtag"
  />
</template>
