<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterLink } from "vue-router";
import { listFollows, type FollowingResponse } from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import NotificationActorCard from "@/components/notifications/NotificationActorCard.vue";

// ``/@username/follows`` — the actors this user follows, mirroring
// ``/@username/followers``. Rows carry a ``pending`` badge while the
// outbound follow request awaits approval (visible to the owner only —
// other viewers only ever receive ``accepted`` rows).
const { t } = useI18n();
const route = useRoute();

const username = computed(() => String(route.params.username));
const offset = ref(0);
const limit = 20;

const follows = ref<FollowingResponse[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);

const hasMore = computed(
  () => offset.value + follows.value.length < total.value,
);

async function load(replace = true) {
  loading.value = true;
  error.value = null;
  try {
    const result = await listFollows(username.value, {
      limit,
      offset: offset.value,
    });
    if (replace) {
      follows.value = result.follows;
    } else {
      follows.value.push(...result.follows);
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
  <main class="user-follows">
    <AppPageTitle class="user-follows__title" icon="user-plus">{{
      t("profile.followsPage.title")
    }}</AppPageTitle>
    <RouterLink
      class="user-follows__back"
      :to="{ name: 'userProfile', params: { username } }"
      >@{{ username }}</RouterLink
    >

    <div v-if="loading && !follows.length" class="user-follows__loading">
      <SkeletonLoader v-for="n in 5" :key="n" variant="card" />
    </div>
    <div v-else-if="error" class="user-follows__error" role="alert">
      {{ error }}
    </div>
    <p v-else-if="!follows.length" class="user-follows__empty">
      {{ t("profile.followsPage.empty") }}
    </p>
    <ul v-else class="user-follows__list">
      <li
        v-for="follow in follows"
        :key="follow.actor_url"
        class="user-follows__item"
      >
        <NotificationActorCard
          class="user-follows__card"
          :actor-url="follow.actor_url"
          :handle="follow.handle"
          :display-name="follow.display_name"
          :avatar-url="follow.avatar_url"
        />
        <span
          v-if="follow.state === 'pending'"
          class="user-follows__pending"
          :title="t('profile.followsPage.pendingHint')"
          >{{ t("profile.followsPage.pending") }}</span
        >
      </li>
    </ul>

    <div v-if="hasMore && !loading" class="user-follows__more">
      <AppButton variant="secondary" @click="loadMore">{{
        t("common.loadMore")
      }}</AppButton>
    </div>
    <SkeletonLoader v-if="loading && follows.length" variant="card" />
  </main>
</template>

<style scoped>
.user-follows {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.user-follows__back {
  color: var(--color-text-muted);
  text-decoration: none;
}

.user-follows__back:hover {
  text-decoration: underline;
}

.user-follows__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.user-follows__item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  position: relative;
}

@media (min-width: 1200px) {
  .user-follows__item {
    width: 750px;
  }
}

.user-follows__card {
  flex: 1;
  min-width: 0;
}

.user-follows__pending {
  position: absolute;
  right: var(--space-8);
  flex-shrink: 0;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-secondary);
  background-color: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-1) var(--space-2);
}

.user-follows__empty,
.user-follows__error {
  color: var(--color-text-muted);
}

.user-follows__error {
  color: var(--color-danger);
}

.user-follows__more {
  display: flex;
  justify-content: center;
}
</style>
