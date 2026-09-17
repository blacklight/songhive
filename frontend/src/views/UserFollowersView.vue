<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterLink } from "vue-router";
import {
  acceptFollowRequest,
  listFollowRequests,
  listFollowers,
  rejectFollowRequest,
  type FollowerResponse,
  type FollowRequestResponse,
} from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import NotificationActorCard from "@/components/notifications/NotificationActorCard.vue";

const { t } = useI18n();
const route = useRoute();
const authStore = useAuthStore();
const toast = useToastStore();

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

// Pending follow requests are private: the "requests" tab only exists when
// the viewed profile belongs to the logged-in user.
const isOwner = computed(() => authStore.user?.username === username.value);
const tab = ref<"followers" | "requests">("followers");
const requests = ref<FollowRequestResponse[]>([]);
const requestsLoaded = ref(false);
const requestsLoading = ref(false);
const requestsTotal = ref(0);
const requestsOffset = ref(0);
const resolving = ref<Set<string>>(new Set());

const requestsHaveMore = computed(
  () => requestsOffset.value + requests.value.length < requestsTotal.value,
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

async function loadRequests(replace = true) {
  if (!isOwner.value) return;
  requestsLoading.value = true;
  try {
    const result = await listFollowRequests({
      limit,
      offset: requestsOffset.value,
    });
    if (replace) {
      requests.value = result.requests;
    } else {
      requests.value.push(...result.requests);
    }
    requestsTotal.value = result.total;
    requestsLoaded.value = true;
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("common.error"),
    });
  } finally {
    requestsLoading.value = false;
  }
}

function loadMoreRequests() {
  requestsOffset.value += limit;
  void loadRequests(false);
}

function selectTab(next: "followers" | "requests") {
  tab.value = next;
  if (next === "requests" && !requestsLoaded.value) {
    requestsOffset.value = 0;
    void loadRequests();
  }
}

async function decideRequest(request: FollowRequestResponse, accept: boolean) {
  if (resolving.value.has(request.actor_url)) return;
  resolving.value = new Set([...resolving.value, request.actor_url]);
  try {
    if (accept) {
      await acceptFollowRequest(request.actor_url);
    } else {
      await rejectFollowRequest(request.actor_url);
    }
    requests.value = requests.value.filter(
      (entry) => entry.actor_url !== request.actor_url,
    );
    requestsTotal.value = Math.max(0, requestsTotal.value - 1);
    toast.push({
      type: "success",
      message: t(
        accept
          ? "profile.followersPage.requestAccepted"
          : "profile.followersPage.requestRejected",
      ),
    });
    if (accept) void load();
  } catch (err) {
    toast.push({
      type: "error",
      message: t("profile.followersPage.requestError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    const next = new Set(resolving.value);
    next.delete(request.actor_url);
    resolving.value = next;
  }
}

onMounted(() => void load());
watch(username, () => {
  offset.value = 0;
  requestsOffset.value = 0;
  requestsLoaded.value = false;
  requests.value = [];
  if (!isOwner.value) tab.value = "followers";
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

    <div v-if="isOwner" class="user-followers__tabs" role="group">
      <AppButton
        size="sm"
        :variant="tab === 'followers' ? 'primary' : 'ghost'"
        @click="selectTab('followers')"
      >
        {{ t("profile.followersPage.tabs.followers") }}
      </AppButton>
      <AppButton
        size="sm"
        :variant="tab === 'requests' ? 'primary' : 'ghost'"
        @click="selectTab('requests')"
      >
        {{ t("profile.followersPage.tabs.requests") }}
      </AppButton>
    </div>

    <template v-if="tab === 'followers'">
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
    </template>

    <template v-else>
      <p class="user-followers__hint">
        {{ t("profile.followersPage.requestsHint") }}
      </p>
      <div
        v-if="requestsLoading && !requests.length"
        class="user-followers__loading"
      >
        <SkeletonLoader v-for="n in 3" :key="n" variant="card" />
      </div>
      <p v-else-if="!requests.length" class="user-followers__empty">
        {{ t("profile.followersPage.requestsEmpty") }}
      </p>
      <ul v-else class="user-followers__list">
        <li
          v-for="request in requests"
          :key="request.actor_url"
          class="user-followers__item"
        >
          <NotificationActorCard
            class="user-followers__card"
            :actor-url="request.actor_url"
            :display-name="request.display_name"
            :avatar-url="request.avatar_url"
          />
          <div class="user-followers__request-actions">
            <AppButton
              size="sm"
              :disabled="resolving.has(request.actor_url)"
              @click="decideRequest(request, true)"
            >
              {{ t("profile.followersPage.accept") }}
            </AppButton>
            <AppButton
              size="sm"
              variant="danger"
              :disabled="resolving.has(request.actor_url)"
              @click="decideRequest(request, false)"
            >
              {{ t("profile.followersPage.reject") }}
            </AppButton>
          </div>
        </li>
      </ul>

      <div
        v-if="requestsHaveMore && !requestsLoading"
        class="user-followers__more"
      >
        <AppButton variant="secondary" @click="loadMoreRequests">{{
          t("common.loadMore")
        }}</AppButton>
      </div>
      <SkeletonLoader
        v-if="requestsLoading && requests.length"
        variant="card"
      />
    </template>
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

.user-followers__tabs {
  display: flex;
  gap: var(--space-2);
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

.user-followers__request-actions {
  display: flex;
  gap: var(--space-2);
  flex-shrink: 0;
}

.user-followers__hint,
.user-followers__empty,
.user-followers__error {
  color: var(--color-text-muted);
}

.user-followers__hint {
  margin: 0;
  font-size: 0.875rem;
}

.user-followers__error {
  color: var(--color-danger);
}

.user-followers__more {
  display: flex;
  justify-content: center;
}
</style>
