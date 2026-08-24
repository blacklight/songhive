<script setup lang="ts">
import { useRoute } from "vue-router";

const route = useRoute();
const playlistId = route.params.id as string;
</script>

<template>
  <div class="playlist">
    <h1>Playlist</h1>
    <p>Playlist details for {{ playlistId }}</p>
  </div>
</template>
