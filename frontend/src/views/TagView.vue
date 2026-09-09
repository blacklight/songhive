<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import {
  deleteTag,
  listTagItems,
  type ListTagItemsParams,
  type TaggedItemType,
} from "@/api/tags";
import TagDetailView, {
  type ListParams,
} from "@/components/tags/TagDetailView.vue";

const route = useRoute();
const tagName = computed(() => String(route.params.name));
const availableTypes: TaggedItemType[] = [
  "artist",
  "album",
  "track",
  "playlist",
  "library",
];

function loadTagItems(name: string, params: ListParams) {
  return listTagItems(name, params as ListTagItemsParams);
}
</script>

<template>
  <TagDetailView
    kind="tag"
    :name="tagName"
    :available-types="availableTypes"
    :load-items="loadTagItems"
    :delete-item="deleteTag"
  />
</template>
