<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterView, RouterLink } from "vue-router";
import {
  blockActor,
  followActor,
  getPublic,
  muteActor,
  subscribeToUserActivity,
  unblockActor,
  unfollowActor,
  unmuteActor,
  unsubscribeFromUserActivity,
  type PublicUserWithModeration,
} from "@/api/users";
import { moderateUser, unmoderateUser } from "@/api/admin";
import { createReport, REPORT_REASONS, type ReportReason } from "@/api/reports";
import { getApiErrorMessage } from "@/api/client";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import FeedButton from "@/components/ui/FeedButton.vue";
import { userFeedUrls } from "@/utils/feeds";
import { useFeedLinks } from "@/composables/useFeedLinks";
import { useConfirm } from "@/composables/useConfirm";
import AppModal from "@/components/feedback/AppModal.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import RichText from "@/components/RichText.vue";
import StatusComposer from "@/components/statuses/StatusComposer.vue";
import RemoteProfileView from "@/views/RemoteProfileView.vue";
import { useAuthStore } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { useToastStore } from "@/stores/toast";
import { formatDate } from "@/i18n";

const { t } = useI18n();
const route = useRoute();
const instanceStore = useInstanceStore();
const authStore = useAuthStore();
const toast = useToastStore();

const instanceDomain = useInstanceDomain();

const username = computed(() => String(route.params.username));
// ``user@domain`` handles route here too — remote actors are resolved
// through the remote lookup API and rendered by RemoteProfileView.
const isRemoteHandle = computed(() => username.value.includes("@"));
// Remote actors have no local feed — only local profiles get feed links.
const feedUrls = computed(() =>
  isRemoteHandle.value ? undefined : userFeedUrls(username.value),
);
useFeedLinks(feedUrls);
const profile = computed<PublicUserWithModeration | null>(() => data.value);

// The compose button only makes sense on one's own profile — statuses are
// recorded on the author's ``user`` entity and listed in the posts tab.
const isOwnProfile = computed(
  () => authStore.user?.username === username.value,
);
const composerOpen = ref(false);
// Bump to remount the active tab after posting so the new status shows up.
const tabVersion = ref(0);

function onStatusSubmitted() {
  composerOpen.value = false;
  tabVersion.value++;
  toast.push({
    type: "success",
    message: t("statusComposer.published"),
  });
}

// Fully-qualified ``@user@domain`` handle — pasteable into other
// federated clients without interpolating the domain by hand.
const fqn = computed(() =>
  instanceDomain.value
    ? `@${username.value}@${instanceDomain.value}`
    : `@${username.value}`,
);

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

const data = ref<PublicUserWithModeration | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const followBusy = ref(false);
const activityBusy = ref(false);
// Limited profiles hide the timeline behind an explicit "show anyway"
// opt-in for everyone except the owner and accepted followers.
const revealLimited = ref(false);
const limitedTimelineGated = computed(
  () =>
    !!profile.value?.limited &&
    !isOwnProfile.value &&
    profile.value?.follow_state !== "accepted" &&
    !revealLimited.value,
);

// The follow button and the activity bell only appear to logged-in
// visitors on someone else's local profile — the viewer-relative
// ``follow_state``/``activity_subscribed`` fields drive their state.
const showProfileActions = computed(
  () =>
    !!authStore.user &&
    !isOwnProfile.value &&
    !isRemoteHandle.value &&
    !!profile.value,
);

async function toggleFollow() {
  if (!data.value || followBusy.value) return;
  followBusy.value = true;
  try {
    if (data.value.follow_state) {
      // Local targets resolve through the username.
      await unfollowActor(username.value);
      data.value.follow_state = null;
    } else {
      const row = await followActor(username.value);
      data.value.follow_state = row.state;
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

async function toggleActivitySubscription() {
  if (!data.value || activityBusy.value) return;
  activityBusy.value = true;
  try {
    if (data.value.activity_subscribed) {
      await unsubscribeFromUserActivity(username.value);
      data.value.activity_subscribed = false;
    } else {
      const state = await subscribeToUserActivity(username.value);
      data.value.activity_subscribed = state.activity_subscribed;
      // Subscribing also follows the user — reflect it on the follow
      // button without a reload.
      if (state.follow_state) {
        data.value.follow_state = state.follow_state;
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

// Moderation: mute/block are viewer actions, limit/suspend are admin
// actions. ``data.value.muted`` etc. are viewer-relative flags reported
// by the profile endpoint.
const moderationBusy = ref(false);
const reasonModalOpen = ref(false);
const pendingAdminAction = ref<"limit" | "suspend" | null>(null);
const moderationReason = ref("");

// Mastodon-style report flow: reason + optional comment goes to the
// local moderators; remote profiles also offer forwarding.
const reportModalOpen = ref(false);
const reportReason = ref<ReportReason>("spam");
const reportComment = ref("");
const reportBusy = ref(false);
const reportReasonOptions = computed(() =>
  REPORT_REASONS.map((value) => ({
    value,
    label: t(`moderation.reportReasons.${value}`),
  })),
);

const { confirm } = useConfirm();

const showModerationActions = computed(
  () => showProfileActions.value || (authStore.isAdmin && !isOwnProfile.value),
);

const moderationActions = computed(() => [
  {
    key: "mute",
    label: data.value?.muted ? t("moderation.unmute") : t("moderation.mute"),
    icon: "volume-xmark",
    variant: "secondary" as const,
    visible: showProfileActions.value,
  },
  {
    key: "block",
    label: data.value?.blocked
      ? t("moderation.unblock")
      : t("moderation.block"),
    icon: "ban",
    variant: "danger" as const,
    visible: showProfileActions.value,
  },
  {
    key: "report",
    label: t("moderation.report"),
    icon: "flag",
    variant: "secondary" as const,
    visible: showProfileActions.value,
  },
  {
    key: "limit",
    label: t("moderation.limit"),
    icon: "arrow-down-wide-short",
    variant: "secondary" as const,
    visible:
      authStore.isAdmin &&
      !isOwnProfile.value &&
      !data.value?.limited &&
      !data.value?.suspended,
  },
  {
    key: "suspend",
    label: t("moderation.suspend"),
    icon: "gavel",
    variant: "danger" as const,
    visible: authStore.isAdmin && !isOwnProfile.value && !data.value?.suspended,
  },
  {
    key: "clear",
    label: t("moderation.clear"),
    icon: "rotate-left",
    variant: "secondary" as const,
    visible:
      authStore.isAdmin &&
      !isOwnProfile.value &&
      !!(data.value?.limited || data.value?.suspended),
  },
]);

async function applyAdminModeration(
  action: "limit" | "suspend",
  reason: string,
) {
  if (!data.value) return;
  const row = await moderateUser({
    actor_url: username.value,
    action,
    reason: reason || null,
  });
  data.value.limited = row.action === "limit";
  data.value.suspended = row.action === "suspend";
}

async function onModerationAction(key: string) {
  if (!data.value || moderationBusy.value) return;
  if (key === "limit" || key === "suspend") {
    pendingAdminAction.value = key;
    moderationReason.value = "";
    reasonModalOpen.value = true;
    return;
  }
  if (key === "report") {
    reportReason.value = "spam";
    reportComment.value = "";
    reportModalOpen.value = true;
    return;
  }
  // Limit/suspend go through the reason modal (a confirmation gate of
  // its own); mute/block apply immediately, so confirm them first —
  // undo actions stay one-click.
  if (key === "mute" && !data.value.muted) {
    const ok = await confirm({
      title: t("moderation.muteConfirmTitle"),
      message: t("moderation.muteConfirmMessage", {
        name: data.value.display_name || data.value.username,
      }),
      confirmLabel: t("moderation.mute"),
    });
    if (!ok) return;
  }
  if (key === "block" && !data.value.blocked) {
    const ok = await confirm({
      title: t("moderation.blockConfirmTitle"),
      message: t("moderation.blockConfirmMessage", {
        name: data.value.display_name || data.value.username,
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
        if (data.value.muted) {
          await unmuteActor(username.value);
          data.value.muted = false;
        } else {
          await muteActor(username.value);
          data.value.muted = true;
        }
        break;
      case "block":
        if (data.value.blocked) {
          await unblockActor(username.value);
          data.value.blocked = false;
        } else {
          await blockActor(username.value);
          data.value.blocked = true;
          // Blocking severs the follow relationship.
          data.value.follow_state = null;
        }
        break;
      case "clear":
        await unmoderateUser(username.value);
        data.value.limited = false;
        data.value.suspended = false;
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
  if (!pendingAdminAction.value) return;
  moderationBusy.value = true;
  try {
    await applyAdminModeration(
      pendingAdminAction.value,
      moderationReason.value.trim(),
    );
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
  if (!data.value || reportBusy.value) return;
  reportBusy.value = true;
  try {
    await createReport({
      target_type: "user",
      target_id: username.value,
      reason: reportReason.value,
      description: reportComment.value.trim() || null,
      forward: false,
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

async function loadProfile() {
  if (isRemoteHandle.value) {
    // RemoteProfileView loads the actor itself.
    loading.value = false;
    error.value = null;
    return;
  }
  loading.value = true;
  error.value = null;
  revealLimited.value = false;
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
    await navigator.clipboard.writeText(fqn.value);
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
  <RemoteProfileView v-if="isRemoteHandle" :handle="username" />
  <div v-else-if="loading" class="user-profile">
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
        <div
          v-if="
            profile.suspended ||
            profile.limited ||
            profile.muted ||
            profile.blocked
          "
          class="user-profile__moderation-badges"
        >
          <span
            v-if="profile.suspended"
            class="user-profile__badge user-profile__badge--danger"
          >
            {{ t("moderation.suspendedBadge") }}
          </span>
          <span v-else-if="profile.limited" class="user-profile__badge">
            {{ t("moderation.limitedBadge") }}
          </span>
          <span
            v-if="profile.blocked"
            class="user-profile__badge user-profile__badge--danger"
          >
            {{ t("moderation.blockedBadge") }}
          </span>
          <span v-else-if="profile.muted" class="user-profile__badge">
            {{ t("moderation.mutedBadge") }}
          </span>
        </div>
        <p class="user-profile__handle">
          {{ fqn }}
          <AppButton
            size="sm"
            variant="ghost"
            icon="copy"
            :title="t('profile.copyHandle')"
            @click="copyHandle"
          />
        </p>

        <div class="user-profile__actions">
          <AppButton
            v-if="showProfileActions"
            size="sm"
            :variant="profile.follow_state ? 'secondary' : 'primary'"
            :disabled="followBusy"
            class="user-profile__follow"
            @click="toggleFollow"
          >
            {{
              profile.follow_state === "accepted"
                ? t("profile.unfollow")
                : profile.follow_state === "pending"
                  ? t("profile.followRequested")
                  : t("profile.follow")
            }}
          </AppButton>
          <FeedButton v-if="feedUrls" :urls="feedUrls" />
          <AppButton
            v-if="showProfileActions"
            size="sm"
            variant="secondary"
            icon="bell"
            :icon-variant="profile.activity_subscribed ? 'solid' : 'regular'"
            :loading="activityBusy"
            :aria-pressed="profile.activity_subscribed"
            :title="
              profile.activity_subscribed
                ? t('profile.activityNotifications.unsubscribe')
                : t('profile.activityNotifications.subscribe')
            "
            class="user-profile__activity-bell"
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

        <div class="user-profile__counts">
          <RouterLink
            :to="{ name: 'userFollowers', params: { username } }"
            class="user-profile__followers"
          >
            <AppIcon name="users" />
            {{ t("profile.followers", profile.followers_count ?? 0) }}
          </RouterLink>
          <RouterLink
            :to="{ name: 'userFollows', params: { username } }"
            class="user-profile__followers"
          >
            <AppIcon name="user-plus" />
            {{ t("profile.following", profile.follows_count ?? 0) }}
          </RouterLink>
        </div>

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

    <AppButton
      v-if="isOwnProfile"
      size="sm"
      icon="pen-to-square"
      class="user-profile__compose"
      @click="composerOpen = true"
    >
      {{ t("profile.compose") }}
    </AppButton>

    <div
      v-if="profile.limited"
      class="user-profile__limited-notice"
      role="note"
    >
      <AppIcon name="arrow-down-wide-short" spacing="right" />
      {{ t("moderation.limitedNotice") }}
      <AppButton
        v-if="limitedTimelineGated"
        size="sm"
        variant="secondary"
        class="user-profile__reveal"
        @click="revealLimited = true"
      >
        {{ t("moderation.showAnyway") }}
      </AppButton>
    </div>

    <RouterView v-slot="{ Component }">
      <component
        :is="Component"
        :key="`${username}:${tabVersion}`"
        :reveal="revealLimited"
      />
    </RouterView>

    <AppModal
      :open="composerOpen"
      :title="t('statusComposer.title')"
      @close="composerOpen = false"
    >
      <StatusComposer autofocus @submitted="onStatusSubmitted" />
    </AppModal>

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
        class="user-profile__reason-form"
        @submit.prevent="confirmAdminModeration"
      >
        <label class="user-profile__reason-label" for="moderation-reason">
          {{ t("moderation.reason") }}
        </label>
        <input
          id="moderation-reason"
          v-model="moderationReason"
          type="text"
          class="user-profile__reason-input"
          :placeholder="t('moderation.reasonPlaceholder')"
        />
        <div class="user-profile__reason-actions">
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
      <form class="user-profile__reason-form" @submit.prevent="submitReport">
        <AppSelect
          v-model="reportReason"
          :label="t('moderation.reportReason')"
          :options="reportReasonOptions"
        />
        <label class="user-profile__reason-label" for="user-report-comment">
          {{ t("moderation.reportComment") }}
        </label>
        <textarea
          id="user-report-comment"
          v-model="reportComment"
          class="user-profile__reason-input"
          rows="4"
          :placeholder="t('moderation.reportCommentPlaceholder')"
        />
        <div class="user-profile__reason-actions">
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
.user-profile {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.user-profile__links a {
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

.user-profile__moderation-badges {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.user-profile__badge {
  font-size: 0.8rem;
  background-color: var(--color-surface-raised);
  color: var(--color-text-secondary);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-lg);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.user-profile__badge--danger {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.user-profile__limited-notice {
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

.user-profile__reveal {
  margin-left: auto;
}

.user-profile__handle {
  margin: calc(-1 * var(--space-2)) 0 0 0;
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

.user-profile__links > tr td:first-child {
  font-weight: 500;
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

.user-profile__counts {
  display: flex;
  gap: var(--space-4);
}

.user-profile__actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.user-profile__follow {
  align-self: flex-start;
}

.user-profile__activity-bell[aria-pressed="true"] {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.user-profile__followers {
  color: var(--color-text-secondary);
  font-size: 1.05em;
  margin: var(--space-1) 0;
  text-decoration: none;
}

.user-profile__followers:hover {
  color: var(--color-text);
  text-decoration: underline;
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

.user-profile__compose {
  margin-right: auto;
  align-self: center;
  flex-shrink: 0;
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

.user-profile__reason-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.user-profile__reason-input {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
}

.user-profile__reason-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
