<script setup lang="ts">
import { useRoute } from "vue-router";

const route = useRoute();
const artistId = route.params.id as string;
</script>

<template>
  <div class="artist">
    <h1>Artist</h1>
    <p>Artist details for {{ artistId }}</p>
  </div>
</template>
