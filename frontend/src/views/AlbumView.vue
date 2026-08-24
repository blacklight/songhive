<script setup lang="ts">
import { useRoute } from "vue-router";

const route = useRoute();
const albumId = route.params.id as string;
</script>

<template>
  <div class="album">
    <h1>Album</h1>
    <p>Album details for {{ albumId }}</p>
  </div>
</template>
