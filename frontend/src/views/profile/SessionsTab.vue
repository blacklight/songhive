<script setup lang="ts">
import { useI18n } from "vue-i18n";
import AppBanner from "@/components/feedback/AppBanner.vue";

// Backend gap: no session list/revoke or OAuth grant endpoints exist yet.
// This tab becomes functional when those endpoints land.

const { t } = useI18n();
</script>

<template>
  <AppBanner type="info" :title="t('profile.tabs.sessions')">
    {{ t("profile.sessions.disabled") }}
  </AppBanner>
</template>
