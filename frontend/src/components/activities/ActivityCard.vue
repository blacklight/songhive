<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import type { ActivityResponse } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { useActivitiesStore } from "@/stores/activities";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import ActivityEditModal from "./ActivityEditModal.vue";

const props = defineProps<{ activity: ActivityResponse }>();

const { t } = useI18n();
const store = useActivitiesStore();
const authStore = useAuthStore();
const confirmStore = useConfirmStore();
const toast = useToastStore();

const editOpen = ref(false);

const URN_PREFIX = "urn:songhive:user:";

const TYPE_ICONS: Record<string, string> = {
  like: "heart",
  announce: "retweet",
  reply: "reply",
  quote: "quote-left",
  mention: "at",
  update: "pen-to-square",
  delete: "trash",
  webmention: "link",
};

const VISIBILITY_ICONS: Record<string, string> = {
  public: "globe",
  followers: "user-group",
  mentioned: "at",
  local: "house",
  private: "lock",
};

function parseActor(actor: string): { shortName: string; host?: string } {
  if (actor.startsWith(URN_PREFIX)) {
    return { shortName: actor.slice(URN_PREFIX.length) };
  }
  try {
    const url = new URL(actor);
    const shortName =
      url.pathname.split("/").filter(Boolean).pop() ?? url.hostname;
    return { shortName, host: url.hostname };
  } catch {
    return { shortName: actor };
  }
}

const actorShortName = computed(
  () => parseActor(props.activity.source_actor).shortName,
);

const actorName = computed(() => {
  const { shortName, host } = parseActor(props.activity.source_actor);
  if (host && props.activity.source_type === "remote") {
    return `@${shortName}@${host}`;
  }
  return `@${shortName}`;
});

const actorDisplayName = computed(
  () =>
    props.activity.source_actor_display_name?.trim() || actorShortName.value,
);

const actorAvatar = computed(
  () => props.activity.source_actor_avatar_url || undefined,
);

const typeIcon = computed(() => TYPE_ICONS[props.activity.activity_type] ?? "");
const typeLabel = computed(() =>
  props.activity.activity_type === "create"
    ? ""
    : t(`activities.types.${props.activity.activity_type}`),
);

const visibilityIcon = computed(
  () => VISIBILITY_ICONS[props.activity.visibility] ?? "globe",
);
const visibilityLabel = computed(() =>
  t(`activities.visibility.${props.activity.visibility}`),
);

// ``content`` of local activities is sanitized HTML produced by the
// server-side mention/markdown pipeline. Remote content is remote-supplied
// HTML and is reduced to plain text instead of being trusted.
const trustedHtml = computed(
  () => props.activity.source_type === "local" && !!props.activity.content,
);

const plainContent = computed(() => {
  if (trustedHtml.value) return "";
  const raw = props.activity.content ?? props.activity.content_source ?? "";
  if (!raw) return "";
  const doc = new DOMParser().parseFromString(raw, "text/html");
  return doc.body.textContent ?? "";
});

const canEdit = computed(
  () =>
    authStore.isAuthenticated &&
    (authStore.isAdmin || authStore.user?.id === props.activity.owner_user_id),
);
const canLike = computed(
  () =>
    authStore.isAuthenticated &&
    props.activity.activity_type !== "like" &&
    props.activity.activity_type !== "delete",
);
const liked = computed(() => store.isLiked(props.activity.id));
const liking = computed(() => store.isLiking(props.activity.id));
const deleting = computed(() => store.isDeleting(props.activity.id));

async function like() {
  try {
    await store.like(props.activity);
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("activities.likeError"),
    });
  }
}

async function remove() {
  const confirmed = await confirmStore.open({
    title: t("activities.delete.title"),
    message: t("activities.delete.confirm"),
    confirmLabel: t("common.delete"),
    danger: true,
  });
  if (!confirmed) return;
  try {
    await store.remove(props.activity.id);
    toast.push({ type: "success", message: t("activities.delete.done") });
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err) || t("activities.delete.error"),
    });
  }
}
</script>

<template>
  <article class="activity-card">
    <header class="activity-card__header">
      <AppAvatar :src="actorAvatar" :name="actorDisplayName" size="sm" />
      <div class="activity-card__meta">
        <span class="activity-card__actor">
          <span class="activity-card__display-name">{{
            actorDisplayName
          }}</span>
          <span class="activity-card__handle">{{ actorName }}</span>
        </span>
        <span class="activity-card__time">{{
          formatDateTime(activity.published_at)
        }}</span>
      </div>
      <div class="activity-card__badges">
        <span v-if="typeLabel" class="activity-card__type" :title="typeLabel">
          <AppIcon v-if="typeIcon" :name="typeIcon" spacing="right" />{{
            typeLabel
          }}
        </span>
        <AppIcon
          :name="visibilityIcon"
          :title="visibilityLabel"
          :aria-label="visibilityLabel"
        />
      </div>
    </header>

    <div
      v-if="trustedHtml"
      class="activity-card__content"
      v-html="activity.content"
    />
    <p v-else-if="plainContent" class="activity-card__content">
      {{ plainContent }}
    </p>

    <footer v-if="canLike || canEdit" class="activity-card__actions">
      <AppButton
        v-if="canLike"
        variant="ghost"
        size="sm"
        icon="heart"
        :icon-variant="liked ? 'solid' : 'regular'"
        :disabled="liked"
        :loading="liking"
        :title="liked ? t('activities.liked') : t('activities.like')"
        :aria-label="liked ? t('activities.liked') : t('activities.like')"
        @click="like"
      >
        {{ liked ? t("activities.liked") : t("activities.like") }}
      </AppButton>
      <AppButton
        v-if="canEdit"
        variant="ghost"
        size="sm"
        icon="pen-to-square"
        :title="t('common.edit')"
        :aria-label="t('common.edit')"
        @click="editOpen = true"
      />
      <AppButton
        v-if="canEdit"
        variant="ghost"
        size="sm"
        icon="trash"
        :loading="deleting"
        :title="t('common.delete')"
        :aria-label="t('common.delete')"
        @click="remove"
      />
    </footer>

    <ActivityEditModal
      :open="editOpen"
      :activity="activity"
      @close="editOpen = false"
    />
  </article>
</template>

<style scoped>
.activity-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

@media (min-width: 75rem) {
  .activity-card {
    width: calc(70rem - var(--sidebar-width));
  }
}

.activity-card__header {
  display: flex;
  gap: var(--space-3);
}

.activity-card__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.activity-card__actor {
  display: flex;
  flex-wrap: wrap;
  flex-direction: column;
  align-items: baseline;
  word-break: break-all;
}

.activity-card__display-name {
  font-weight: 600;
  color: var(--color-text);
}

.activity-card__handle {
  color: var(--color-text-muted);
  font-size: 0.9em;
}

.activity-card__time {
  font-size: 0.8rem;
  color: var(--color-text-muted);
}

.activity-card__badges {
  display: inline-flex;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.8rem;
}

.activity-card__type {
  display: inline-flex;
  white-space: nowrap;
}

.activity-card__content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.activity-card__actions {
  display: flex;
  gap: var(--space-2);
}

:deep(.activity-card__content a) {
  color: var(--color-text-link);
}

:deep(.activity-card__content a:visited) {
  color: var(--color-text-link);
}

a:hover {
  color: var(--color-text-hover);
}
</style>
