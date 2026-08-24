<script setup lang="ts">
import { RouterView } from "vue-router";
import ConfirmDialog from "@/components/feedback/ConfirmDialog.vue";
</script>

<template>
  <RouterView />
  <ConfirmDialog />
</template>

<style>
body {
  font-family:
    -apple-system, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Fira Sans",
    "Open Sans", "Droid Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
  letter-spacing: 0.0333em;
  margin: 0;
}
</style>
