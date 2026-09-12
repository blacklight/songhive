<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterLink } from "vue-router";
import { listFollowers, type FollowerResponse } from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import NotificationActorCard from "@/components/notifications/NotificationActorCard.vue";

const { t } = useI18n();
const route = useRoute();

const username = computed(() => String(route.params.username));
const offset = ref(0);
const limit = 20;

const followers = ref<FollowerResponse[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);

const hasMore = computed(
  () => offset.value + followers.value.length < total.value,
);

async function load(replace = true) {
  loading.value = true;
  error.value = null;
  try {
    const result = await listFollowers(username.value, {
      limit,
      offset: offset.value,
    });
    if (replace) {
      followers.value = result.followers;
    } else {
      followers.value.push(...result.followers);
    }
    total.value = result.total;
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("common.error");
  } finally {
    loading.value = false;
  }
}

function loadMore() {
  offset.value += limit;
  void load(false);
}

onMounted(() => void load());
watch(username, () => {
  offset.value = 0;
  void load();
});
</script>

<template>
  <main class="user-followers">
    <AppPageTitle class="user-followers__title" icon="users">{{
      t("profile.followersPage.title")
    }}</AppPageTitle>
    <RouterLink
      class="user-followers__back"
      :to="{ name: 'userProfile', params: { username } }"
      >@{{ username }}</RouterLink
    >

    <div v-if="loading && !followers.length" class="user-followers__loading">
      <SkeletonLoader v-for="n in 5" :key="n" variant="card" />
    </div>
    <div v-else-if="error" class="user-followers__error" role="alert">
      {{ error }}
    </div>
    <p v-else-if="!followers.length" class="user-followers__empty">
      {{ t("profile.followersPage.empty") }}
    </p>
    <ul v-else class="user-followers__list">
      <li
        v-for="follower in followers"
        :key="follower.actor_url"
        class="user-followers__item"
      >
        <NotificationActorCard
          class="user-followers__card"
          :actor-url="follower.actor_url"
          :display-name="follower.display_name"
          :avatar-url="follower.avatar_url"
        />
      </li>
    </ul>

    <div v-if="hasMore && !loading" class="user-followers__more">
      <AppButton variant="secondary" @click="loadMore">{{
        t("common.loadMore")
      }}</AppButton>
    </div>
    <SkeletonLoader v-if="loading && followers.length" variant="card" />
  </main>
</template>

<style scoped>
.user-followers {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.user-followers__back {
  color: var(--color-text-muted);
  text-decoration: none;
}

.user-followers__back:hover {
  text-decoration: underline;
}

.user-followers__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.user-followers__item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

@media (min-width: 1200px) {
  .user-followers__item {
    width: 750px;
  }
}

.user-followers__card {
  flex: 1;
  min-width: 0;
}

.user-followers__empty,
.user-followers__error {
  color: var(--color-text-muted);
}

.user-followers__error {
  color: var(--color-danger);
}

.user-followers__more {
  display: flex;
  justify-content: center;
}
</style>
