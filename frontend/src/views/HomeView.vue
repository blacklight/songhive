<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import { useTimelineStore } from "@/stores/timeline";
import HomeAnonShelves from "@/components/home/HomeAnonShelves.vue";
import HomeAuthShelves from "@/components/home/HomeAuthShelves.vue";
import HomeFeed from "@/components/home/HomeFeed.vue";
import HomeGreeting from "@/components/home/HomeGreeting.vue";
import HomeHero from "@/components/home/HomeHero.vue";
import AppTabs, { type Tab } from "@/components/ui/AppTabs.vue";

/**
 * The home page splits by audience: authenticated users get a greeting plus
 * a compact "your music" shelf zone; anonymous visitors get the instance
 * hero, public-catalogue shelves, genre chips. In both cases the media
 * shelves and the activity feed sit behind two tabs — shelves first — so
 * the feed no longer trails below the whole catalogue.
 *
 * In single-user mode anonymous visits to ``/`` never reach this view —
 * the server redirect (and the router guard as a fast path) send them to
 * ``/@{username}`` first.
 */
const authStore = useAuthStore();
const timelineStore = useTimelineStore();
const { t } = useI18n();

const authenticated = computed(() => authStore.isAuthenticated);

const tabs = computed<Tab[]>(() => [
  { value: "music", label: t("pages.home.tabs.music") },
  { value: "activity", label: t("pages.home.tabs.activity") },
]);
const activeTab = ref("music");

// The feed's default scope depends on the audience, so drop the cached
// state when the session changes (login/logout while on the page).
watch(
  () => authStore.isAuthenticated,
  () => timelineStore.$reset(),
);
</script>

<template>
  <div class="home">
    <HomeGreeting v-if="authenticated" @posted="timelineStore.refresh()" />
    <HomeHero v-else />

    <AppTabs v-model="activeTab" :tabs="tabs" class="home__tabs" />

    <div v-show="activeTab === 'music'" class="home__panel">
      <HomeAuthShelves v-if="authenticated" />
      <HomeAnonShelves v-else />
    </div>
    <div v-show="activeTab === 'activity'" class="home__panel">
      <HomeFeed :key="String(authenticated)" :authenticated="authenticated" />
    </div>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.home__panel {
  display: flex;
  flex-direction: column;
}
</style>
