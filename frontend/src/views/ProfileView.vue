<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import ProfileTab from "./profile/ProfileTab.vue";
import ApiTokensTab from "./profile/ApiTokensTab.vue";
import SessionsTab from "./profile/SessionsTab.vue";

const { t } = useI18n();
const route = useRoute();

const tab = computed(() => {
  const raw = route.query.tab;
  const value = Array.isArray(raw) ? raw[0] : raw;
  return typeof value === "string" &&
    ["profile", "apiTokens", "sessions"].includes(value)
    ? value
    : "profile";
});

const tabs = [
  { key: "profile", label: t("profile.tabs.profile") },
  { key: "apiTokens", label: t("profile.tabs.apiTokens") },
  { key: "sessions", label: t("profile.tabs.sessions") },
];

const currentComponent = computed(() => {
  switch (tab.value) {
    case "apiTokens":
      return ApiTokensTab;
    case "sessions":
      return SessionsTab;
    default:
      return ProfileTab;
  }
});
</script>

<template>
  <main class="profile-view">
    <h1 class="profile-view__title">{{ t("profile.title") }}</h1>

    <nav class="profile-view__tabs" aria-label="Profile tabs">
      <RouterLink
        v-for="item in tabs"
        :key="item.key"
        :to="{ path: '/profile', query: { tab: item.key } }"
        :class="['profile-view__tab', { 'profile-view__tab--active': tab === item.key }]"
      >
        {{ item.label }}
      </RouterLink>
    </nav>

    <section class="profile-view__panel">
      <component :is="currentComponent" />
    </section>
  </main>
</template>

<style scoped>
.profile-view {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.profile-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.profile-view__tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
}

.profile-view__tab {
  padding: var(--space-2) var(--space-4);
  border-bottom: 2px solid transparent;
  color: var(--color-text-muted);
  text-decoration: none;
  font-weight: 500;
}

.profile-view__tab:hover,
.profile-view__tab--active {
  color: var(--color-text);
  border-bottom-color: var(--color-accent);
}

.profile-view__panel {
  padding: var(--space-4) 0;
}
</style>
