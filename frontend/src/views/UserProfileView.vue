<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterView, RouterLink } from "vue-router";
import { getPublic, type PublicUserResponse } from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import RichText from "@/components/RichText.vue";
import { useInstanceStore } from "@/stores/instance";
import { formatDate } from "@/i18n";

const { t } = useI18n();
const route = useRoute();
const instanceStore = useInstanceStore();

const instanceDomain = computed(() => {
  if (instanceStore.instance?.uri) {
    try {
      return new URL(instanceStore.instance.uri).host;
    } catch {
      // fall back to the browser host
    }
  }
  return window.location.host;
});

const username = computed(() => String(route.params.username));
const profile = computed<PublicUserResponse | null>(() => data.value);

const TABS = [
  { key: "posts", label: t("profile.tabs.posts"), name: "userProfilePosts" },
  {
    key: "activity",
    label: t("profile.tabs.activity"),
    name: "userProfileActivity",
  },
  { key: "tracks", label: t("profile.tabs.tracks"), name: "userProfileTracks" },
  { key: "albums", label: t("profile.tabs.albums"), name: "userProfileAlbums" },
  {
    key: "libraries",
    label: t("profile.tabs.libraries"),
    name: "userProfileLibraries",
  },
  {
    key: "playlists",
    label: t("profile.tabs.playlists"),
    name: "userProfilePlaylists",
  },
] as const;

const data = ref<PublicUserResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

async function loadProfile() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await getPublic(username.value);
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("common.error");
  } finally {
    loading.value = false;
  }
}

async function copyHandle() {
  try {
    await navigator.clipboard.writeText(
      `@${username.value}@${instanceDomain.value}`,
    );
  } catch {
    // ignore
  }
}

onMounted(() => {
  loadProfile();
  instanceStore.load();
});
watch(username, loadProfile);
</script>

<template>
  <div v-if="loading" class="user-profile">
    <SkeletonLoader variant="card" />
  </div>
  <div v-else-if="error" class="user-profile__error" role="alert">
    {{ error }}
  </div>
  <div v-else-if="profile" class="user-profile">
    <header class="user-profile__header">
      <AppAvatar
        :src="profile.avatar_url || ''"
        :name="profile.display_name || profile.username"
        size="lg"
        class="user-profile__avatar"
      />
      <div class="user-profile__info">
        <div class="user-profile__display-name">
          <h1>
            {{ profile.display_name || profile.username }}
          </h1>
          <span v-if="profile.role" class="user-profile__role">{{
            profile.role
          }}</span>
        </div>
        <p class="user-profile__handle">
          @{{ profile.username }}
          <AppButton
            size="sm"
            variant="ghost"
            icon="copy"
            :title="t('profile.copyHandle')"
            @click="copyHandle"
          />
        </p>
        <p v-if="profile.bio" class="user-profile__bio">
          <RichText
            :text="profile.bio ?? ''"
            :instance-domain="instanceDomain"
          />
        </p>
        <table v-if="profile.links?.length" class="user-profile__links">
          <tr v-for="link in profile.links" :key="link.url">
            <td>{{ link.name }}</td>
            <td>
              <a :href="link.url" target="_blank" rel="noopener me">{{
                link.url.replace(/^https?:\/\//, "")
              }}</a>
            </td>
          </tr>
        </table>
        <div class="user-profile__meta">
          <span class="user-profile__joined">
            <span class="user-profile__label">{{ t("users.joined") }}:</span>
            {{ formatDate(profile.created_at) }}
          </span>
        </div>
      </div>
    </header>

    <nav class="user-profile__tabs" aria-label="Profile tabs">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.key"
        :to="{ name: tab.name }"
        class="user-profile__tab"
        :class="{ 'user-profile__tab--active': $route.name === tab.name }"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView :key="username" />
  </div>
</template>

<style scoped>
.user-profile {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.user-profile__links a,
.user-profile__links a:visited {
  color: var(--color-text-link);
}

.user-profile__error {
  padding: var(--space-4);
  color: var(--color-danger);
  background-color: var(--color-surface);
  border-radius: var(--radius-md);
}

.user-profile__header {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
}

.user-profile__avatar {
  flex-shrink: 0;
}

.user-profile__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.user-profile__display-name {
  display: flex;
  align-items: center;
}

.user-profile__display-name h1 {
  margin: 0;
  font-size: 1.5rem;
  display: inline-block;
}

.user-profile__handle {
  margin: 0;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.user-profile__bio {
  font-size: 0.9rem;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.user-profile__links {
  background-color: var(--color-surface);
  margin: 0;
  padding: var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.user-profile__links > tr td {
  padding: var(--space-1) 0;
}

.user-profile__meta {
  display: flex;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.user-profile__joined {
  font-size: 0.9em;
}

.user-profile__label {
  font-weight: 500;
}

.user-profile__role {
  font-size: 0.875rem;
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
  text-transform: capitalize;
  margin-left: var(--space-3);
  padding: calc(1.25 * var(--space-1));
  border-radius: var(--radius-lg);
}

.user-profile__tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
}

.user-profile__tab {
  padding: var(--space-2) var(--space-4);
  border-bottom: 2px solid transparent;
  color: var(--color-text-muted);
  text-decoration: none;
  font-weight: 500;
}

.user-profile__tab--active,
.user-profile__tab:hover {
  color: var(--color-text);
  border-bottom-color: var(--color-accent);
}
</style>
