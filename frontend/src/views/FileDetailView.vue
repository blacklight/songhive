<script setup lang="ts">
import { useI18n } from "vue-i18n";
import PlaceholderView from "./PlaceholderView.vue";

const { t } = useI18n();
</script>

<template>
  <PlaceholderView :name="t('nav.files')" :phase="5" />
</template>
