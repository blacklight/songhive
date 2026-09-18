<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import {
  deleteGenre,
  listGenreItems,
  type GenreItemType,
  type ListGenreItemsParams,
} from "@/api/genres";
import TagDetailView, {
  type ListParams,
} from "@/components/tags/TagDetailView.vue";
import { genreFeedUrls } from "@/utils/feeds";

const route = useRoute();
const genreName = computed(() => String(route.params.name));
const feedUrls = computed(() => genreFeedUrls(genreName.value));
const availableTypes: GenreItemType[] = ["album", "track"];

function loadGenreItems(name: string, params: ListParams) {
  return listGenreItems(name, params as ListGenreItemsParams);
}
</script>

<template>
  <TagDetailView
    kind="genre"
    :name="genreName"
    :available-types="availableTypes"
    :load-items="loadGenreItems"
    :delete-item="deleteGenre"
    :feed-urls="feedUrls"
  />
</template>
