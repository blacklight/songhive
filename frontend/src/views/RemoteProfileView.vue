<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  getRemoteActor,
  getRemoteActorActivities,
  subscribeToRemoteActorActivity,
  unsubscribeFromRemoteActorActivity,
  type RemoteActor,
} from "@/api/remote";
import {
  blockActor,
  followActor,
  muteActor,
  unblockActor,
  unfollowActor,
  unmuteActor,
} from "@/api/users";
import { moderateUser, unmoderateUser } from "@/api/admin";
import { createReport, REPORT_REASONS, type ReportReason } from "@/api/reports";
import type { ActivityResponse } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { useConfirm } from "@/composables/useConfirm";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
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
const activityBusy = ref(false);
// Limited profiles hide the timeline behind an explicit "show anyway"
// opt-in for everyone except accepted followers.
const revealed = ref(false);
const limitedTimelineGated = computed(
  () =>
    !!actor.value?.limited &&
    actor.value?.follow_state !== "accepted" &&
    !revealed.value,
);

async function revealLimited() {
  revealed.value = true;
  try {
    const result = await getRemoteActorActivities(normalizedHandle.value, {
      reveal: true,
    });
    activities.value = result.activities;
  } catch {
    activities.value = [];
  }
}

async function toggleActivitySubscription() {
  if (!actor.value || activityBusy.value) return;
  activityBusy.value = true;
  try {
    if (actor.value.activity_subscribed) {
      await unsubscribeFromRemoteActorActivity(normalizedHandle.value);
      actor.value.activity_subscribed = false;
    } else {
      const state = await subscribeToRemoteActorActivity(
        normalizedHandle.value,
      );
      actor.value.activity_subscribed = state.activity_subscribed;
      // Subscribing also follows the actor — reflect it on the follow
      // button without a reload.
      if (state.follow_state) {
        actor.value.follow_state = state.follow_state;
      }
    }
  } catch (err) {
    toast.push({
      type: "error",
      message: t("profile.activityNotifications.error", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    activityBusy.value = false;
  }
}

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

// Moderation: mute/block are viewer actions, limit/suspend are admin
// actions — all keyed on the actor's canonical URL.
const moderationBusy = ref(false);
const reasonModalOpen = ref(false);
const pendingAdminAction = ref<"limit" | "suspend" | null>(null);
const moderationReason = ref("");

// Report flow: reason + comment to local moderators, with optional
// forwarding to the actor's home instance via ActivityPub ``Flag``.
const reportModalOpen = ref(false);
const reportReason = ref<ReportReason>("spam");
const reportComment = ref("");
const reportForward = ref(false);
const reportBusy = ref(false);
const reportReasonOptions = computed(() =>
  REPORT_REASONS.map((value) => ({
    value,
    label: t(`moderation.reportReasons.${value}`),
  })),
);

const { confirm } = useConfirm();

const moderationActions = computed(() => [
  {
    key: "mute",
    label: actor.value?.muted ? t("moderation.unmute") : t("moderation.mute"),
    icon: "volume-xmark",
    variant: "secondary" as const,
    visible: !!authStore.user,
  },
  {
    key: "block",
    label: actor.value?.blocked
      ? t("moderation.unblock")
      : t("moderation.block"),
    icon: "ban",
    variant: "danger" as const,
    visible: !!authStore.user,
  },
  {
    key: "report",
    label: t("moderation.report"),
    icon: "flag",
    variant: "secondary" as const,
    visible: !!authStore.user,
  },
  {
    key: "limit",
    label: t("moderation.limit"),
    icon: "arrow-down-wide-short",
    variant: "secondary" as const,
    visible:
      authStore.isAdmin && !actor.value?.limited && !actor.value?.suspended,
  },
  {
    key: "suspend",
    label: t("moderation.suspend"),
    icon: "gavel",
    variant: "danger" as const,
    visible: authStore.isAdmin && !actor.value?.suspended,
  },
  {
    key: "clear",
    label: t("moderation.clear"),
    icon: "rotate-left",
    variant: "secondary" as const,
    visible:
      authStore.isAdmin && !!(actor.value?.limited || actor.value?.suspended),
  },
]);

const showModerationActions = computed(
  () => !!authStore.user && moderationActions.value.some((a) => a.visible),
);

async function onModerationAction(key: string) {
  if (!actor.value || moderationBusy.value) return;
  if (key === "limit" || key === "suspend") {
    pendingAdminAction.value = key;
    moderationReason.value = "";
    reasonModalOpen.value = true;
    return;
  }
  if (key === "report") {
    reportReason.value = "spam";
    reportComment.value = "";
    reportForward.value = false;
    reportModalOpen.value = true;
    return;
  }
  // Limit/suspend go through the reason modal (a confirmation gate of
  // its own); mute/block apply immediately, so confirm them first —
  // undo actions stay one-click.
  if (key === "mute" && !actor.value.muted) {
    const ok = await confirm({
      title: t("moderation.muteConfirmTitle"),
      message: t("moderation.muteConfirmMessage", {
        name: actor.value.display_name || actor.value.username,
      }),
      confirmLabel: t("moderation.mute"),
    });
    if (!ok) return;
  }
  if (key === "block" && !actor.value.blocked) {
    const ok = await confirm({
      title: t("moderation.blockConfirmTitle"),
      message: t("moderation.blockConfirmMessage", {
        name: actor.value.display_name || actor.value.username,
      }),
      confirmLabel: t("moderation.block"),
      danger: true,
    });
    if (!ok) return;
  }
  moderationBusy.value = true;
  try {
    switch (key) {
      case "mute":
        if (actor.value.muted) {
          await unmuteActor(actor.value.actor_url);
          actor.value.muted = false;
        } else {
          await muteActor(actor.value.actor_url);
          actor.value.muted = true;
        }
        break;
      case "block":
        if (actor.value.blocked) {
          await unblockActor(actor.value.actor_url);
          actor.value.blocked = false;
        } else {
          await blockActor(actor.value.actor_url);
          actor.value.blocked = true;
          // Blocking severs the follow relationship.
          actor.value.follow_state = null;
        }
        break;
      case "clear":
        await unmoderateUser(actor.value.actor_url);
        actor.value.limited = false;
        actor.value.suspended = false;
        break;
    }
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("common.error"),
    });
  } finally {
    moderationBusy.value = false;
  }
}

async function confirmAdminModeration() {
  if (!actor.value || !pendingAdminAction.value) return;
  moderationBusy.value = true;
  try {
    const row = await moderateUser({
      actor_url: actor.value.actor_url,
      action: pendingAdminAction.value,
      reason: moderationReason.value.trim() || null,
    });
    actor.value.limited = row.action === "limit";
    actor.value.suspended = row.action === "suspend";
    reasonModalOpen.value = false;
    pendingAdminAction.value = null;
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("common.error"),
    });
  } finally {
    moderationBusy.value = false;
  }
}

async function submitReport() {
  if (!actor.value || reportBusy.value) return;
  reportBusy.value = true;
  try {
    await createReport({
      target_type: "actor",
      target_id: actor.value.actor_url,
      reason: reportReason.value,
      description: reportComment.value.trim() || null,
      forward: reportForward.value,
    });
    reportModalOpen.value = false;
    toast.push({ type: "success", message: t("moderation.reportSubmitted") });
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("common.error"),
    });
  } finally {
    reportBusy.value = false;
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  actor.value = null;
  activities.value = [];
  revealed.value = false;
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
          <span
            v-if="actor.suspended"
            class="remote-profile__badge remote-profile__badge--danger"
          >
            {{ t("moderation.suspendedBadge") }}
          </span>
          <span v-else-if="actor.limited" class="remote-profile__badge">
            {{ t("moderation.limitedBadge") }}
          </span>
          <span
            v-if="actor.blocked"
            class="remote-profile__badge remote-profile__badge--danger"
          >
            {{ t("moderation.blockedBadge") }}
          </span>
          <span v-else-if="actor.muted" class="remote-profile__badge">
            {{ t("moderation.mutedBadge") }}
          </span>
        </div>
        <p class="remote-profile__handle">@{{ actor.handle }}</p>
        <div v-if="authStore.user" class="remote-profile__actions">
          <AppButton
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
          <AppButton
            size="sm"
            variant="secondary"
            icon="bell"
            :icon-variant="actor.activity_subscribed ? 'solid' : 'regular'"
            :loading="activityBusy"
            :aria-pressed="actor.activity_subscribed"
            :title="
              actor.activity_subscribed
                ? t('profile.activityNotifications.unsubscribe')
                : t('profile.activityNotifications.subscribe')
            "
            class="remote-profile__activity-bell"
            @click="toggleActivitySubscription"
          />
          <EntityActions
            v-if="showModerationActions"
            :actions="moderationActions"
            :primary-count="0"
            menu-only
            @select="onModerationAction"
          />
        </div>
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

    <div
      v-if="actor.limited"
      class="remote-profile__limited-notice"
      role="note"
    >
      <AppIcon name="arrow-down-wide-short" spacing="right" />
      {{ t("moderation.limitedNotice") }}
      <AppButton
        v-if="limitedTimelineGated"
        size="sm"
        variant="secondary"
        class="remote-profile__reveal"
        @click="revealLimited"
      >
        {{ t("moderation.showAnyway") }}
      </AppButton>
    </div>

    <section class="remote-profile__activities">
      <h2 class="remote-profile__section-title">
        {{ t("remote.cachedActivities") }}
      </h2>
      <p
        v-if="!activities.length && !limitedTimelineGated"
        class="remote-profile__empty"
      >
        {{ t("remote.noCachedActivities") }}
      </p>
      <ActivityCard
        v-for="activity in activities"
        :key="activity.id"
        :activity="activity"
      />
    </section>

    <AppModal
      :open="reasonModalOpen"
      :title="
        pendingAdminAction === 'suspend'
          ? t('moderation.suspend')
          : t('moderation.limit')
      "
      @close="reasonModalOpen = false"
    >
      <form
        class="remote-profile__reason-form"
        @submit.prevent="confirmAdminModeration"
      >
        <label
          class="remote-profile__reason-label"
          for="remote-moderation-reason"
        >
          {{ t("moderation.reason") }}
        </label>
        <input
          id="remote-moderation-reason"
          v-model="moderationReason"
          type="text"
          class="remote-profile__reason-input"
          :placeholder="t('moderation.reasonPlaceholder')"
        />
        <div class="remote-profile__reason-actions">
          <AppButton
            type="button"
            variant="ghost"
            @click="reasonModalOpen = false"
          >
            {{ t("common.cancel") }}
          </AppButton>
          <AppButton
            type="submit"
            :variant="pendingAdminAction === 'suspend' ? 'danger' : 'primary'"
            :loading="moderationBusy"
          >
            {{ t("common.confirm") }}
          </AppButton>
        </div>
      </form>
    </AppModal>

    <AppModal
      :open="reportModalOpen"
      :title="t('moderation.reportTitle')"
      @close="reportModalOpen = false"
    >
      <form class="remote-profile__reason-form" @submit.prevent="submitReport">
        <AppSelect
          v-model="reportReason"
          :label="t('moderation.reportReason')"
          :options="reportReasonOptions"
        />
        <label class="remote-profile__reason-label" for="remote-report-comment">
          {{ t("moderation.reportComment") }}
        </label>
        <textarea
          id="remote-report-comment"
          v-model="reportComment"
          class="remote-profile__reason-input"
          rows="4"
          :placeholder="t('moderation.reportCommentPlaceholder')"
        />
        <AppCheckbox
          v-model="reportForward"
          :label="t('moderation.forwardToRemote', { domain: actor.domain })"
        />
        <div class="remote-profile__reason-actions">
          <AppButton
            type="button"
            variant="ghost"
            @click="reportModalOpen = false"
          >
            {{ t("common.cancel") }}
          </AppButton>
          <AppButton type="submit" :loading="reportBusy">
            {{ t("moderation.reportSubmit") }}
          </AppButton>
        </div>
      </form>
    </AppModal>
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

.remote-profile__badge--danger {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.remote-profile__limited-notice {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-3) var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.remote-profile__reveal {
  margin-left: auto;
}

.remote-profile__handle {
  margin: 0;
  color: var(--color-text-muted);
}

.remote-profile__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.remote-profile__follow {
  align-self: flex-start;
}

.remote-profile__activity-bell[aria-pressed="true"] {
  color: var(--color-accent);
  border-color: var(--color-accent);
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

.remote-profile__reason-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.remote-profile__reason-input {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
}

.remote-profile__reason-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
