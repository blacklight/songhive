<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  getRemoteActor,
  getRemoteActorActivities,
  type RemoteActor,
} from "@/api/remote";
import { followActor, unfollowActor } from "@/api/users";
import type { ActivityResponse } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import RichContent from "@/components/RichContent.vue";
import ActivityCard from "@/components/activities/ActivityCard.vue";

// Remote (federated) actor profile for ``/@user@domain`` routes. The actor
// is resolved — and cached — through ``/remote/actors/{handle}``; the
// activity list only ever shows already-cached materialized posts.
const props = defineProps<{ handle: string }>();
const { t } = useI18n();
const instanceDomain = useInstanceDomain();
const authStore = useAuthStore();
const toast = useToastStore();

const normalizedHandle = computed(() => props.handle.replace(/^@/, ""));
const actor = ref<RemoteActor | null>(null);
const activities = ref<ActivityResponse[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const followBusy = ref(false);

async function toggleFollow() {
  if (!actor.value || followBusy.value) return;
  followBusy.value = true;
  try {
    if (actor.value.follow_state) {
      await unfollowActor(actor.value.actor_url);
      actor.value.follow_state = null;
    } else {
      const row = await followActor(actor.value.actor_url);
      actor.value.follow_state = row.state;
    }
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("common.error"),
    });
  } finally {
    followBusy.value = false;
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  actor.value = null;
  activities.value = [];
  try {
    actor.value = await getRemoteActor(normalizedHandle.value);
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("common.error");
    loading.value = false;
    return;
  }
  try {
    const result = await getRemoteActorActivities(normalizedHandle.value);
    activities.value = result.activities;
  } catch {
    activities.value = [];
  } finally {
    loading.value = false;
  }
}

watch(normalizedHandle, load, { immediate: true });
</script>

<template>
  <div v-if="loading" class="remote-profile">
    <SkeletonLoader variant="card" />
  </div>
  <div v-else-if="error" class="remote-profile__error" role="alert">
    {{ error }}
  </div>
  <div v-else-if="actor" class="remote-profile">
    <header class="remote-profile__header">
      <AppAvatar
        :src="actor.avatar_url || ''"
        :name="actor.display_name || actor.username"
        size="lg"
        class="remote-profile__avatar"
      />
      <div class="remote-profile__info">
        <div class="remote-profile__display-name">
          <h1>{{ actor.display_name || actor.username }}</h1>
          <span class="remote-profile__badge">
            <AppIcon name="globe" spacing="right" />{{ t("remote.badge") }}
          </span>
        </div>
        <p class="remote-profile__handle">@{{ actor.handle }}</p>
        <AppButton
          v-if="authStore.user"
          size="sm"
          :variant="actor.follow_state ? 'secondary' : 'primary'"
          :disabled="followBusy"
          class="remote-profile__follow"
          @click="toggleFollow"
        >
          {{
            actor.follow_state === "accepted"
              ? t("profile.unfollow")
              : actor.follow_state === "pending"
                ? t("profile.followRequested")
                : t("profile.follow")
          }}
        </AppButton>
        <p v-if="actor.unavailable" class="remote-profile__unavailable">
          {{ t("remote.actorUnavailable") }}
        </p>
        <p v-if="actor.summary" class="remote-profile__summary">
          <RichContent
            :html="actor.summary"
            :instance-domain="instanceDomain"
          />
        </p>
        <p class="remote-profile__meta">
          <a
            :href="actor.profile_url || actor.actor_url"
            target="_blank"
            rel="noopener"
            class="remote-profile__origin"
          >
            <AppIcon name="arrow-up-right-from-square" spacing="right" />
            {{ t("remote.viewOriginal", { domain: actor.domain }) }}
          </a>
        </p>
      </div>
    </header>

    <section class="remote-profile__activities">
      <h2 class="remote-profile__section-title">
        {{ t("remote.cachedActivities") }}
      </h2>
      <p v-if="!activities.length" class="remote-profile__empty">
        {{ t("remote.noCachedActivities") }}
      </p>
      <ActivityCard
        v-for="activity in activities"
        :key="activity.id"
        :activity="activity"
      />
    </section>
  </div>
</template>

<style scoped>
.remote-profile {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.remote-profile__error {
  padding: var(--space-4);
  color: var(--color-danger);
  background-color: var(--color-surface);
  border-radius: var(--radius-md);
}

.remote-profile__header {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
}

.remote-profile__avatar {
  flex-shrink: 0;
}

.remote-profile__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.remote-profile__display-name {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.remote-profile__display-name h1 {
  margin: 0;
  font-size: 1.5rem;
  display: inline-block;
}

.remote-profile__badge {
  font-size: 0.875rem;
  background-color: var(--color-surface-raised);
  color: var(--color-text-secondary);
  padding: calc(1.25 * var(--space-1)) var(--space-2);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.remote-profile__handle {
  margin: 0;
  color: var(--color-text-muted);
}

.remote-profile__follow {
  align-self: flex-start;
}

.remote-profile__unavailable {
  margin: 0;
  color: var(--color-warning, var(--color-text-muted));
  font-size: 0.9rem;
}

.remote-profile__summary {
  font-size: 0.9rem;
  margin: 0;
  word-break: break-word;
}

.remote-profile__meta {
  margin: 0;
}

.remote-profile__origin {
  color: var(--color-text-link);
  font-size: 0.9rem;
  text-decoration: none;
}

.remote-profile__origin:hover {
  text-decoration: underline;
}

.remote-profile__activities {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0 auto;
}

.remote-profile__section-title {
  margin: 0;
  font-size: 1.1rem;
}

.remote-profile__empty {
  color: var(--color-text-muted);
}
</style>
