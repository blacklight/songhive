<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  followRemoteObject,
  getRemoteResource,
  unfollowRemoteObject,
  type RemoteObject,
  type RemoteResourceKind,
} from "@/api/remote";
import { getApiErrorMessage } from "@/api/client";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import EntityActions from "@/components/ui/EntityActions.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import RichContent from "@/components/RichContent.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { useCollectionItem } from "@/composables/useCollectionItem";
import {
  remoteObjectPlayableTracks,
  remoteObjectToQueueTrack,
} from "@/utils/remoteObject";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";

// Page for a cached remote resource (track/album/artist/playlist/library).
// Remote resources never become local rows and carry no local management
// actions — editing, sharing, and library controls do not apply — but the
// resource can be bookmarked into the caller's collection and followed
// (object-scoped follow: a followed library delivers new items to the
// instance).
const { t } = useI18n();
const route = useRoute();
const instanceDomain = useInstanceDomain();
const authStore = useAuthStore();
const player = usePlayerStore();
const toastStore = useToastStore();

const object = ref<RemoteObject | null>(null);
const loading = ref(true);
const failed = ref(false);
const followBusy = ref(false);

const kind = computed(
  () => String(route.params.kind ?? "") as RemoteResourceKind,
);

const kindLabel = computed(() => t(`remote.kinds.${kind.value}`, kind.value));

// Remote ``summary``/``content`` are HTML documents — RichContent renders
// them as safe segments (text, links, mentions, tags), never verbatim.
const summaryHtml = computed(
  () => object.value?.summary || object.value?.content || "",
);

const { collectionAction, toggleCollection } = useCollectionItem(
  "remote",
  object,
);

const followLabel = computed(() => {
  const state = object.value?.follow_state;
  if (state === "accepted") return t("remote.unfollow");
  if (state === "pending") return t("remote.followRequested");
  return t("remote.follow", { kind: kindLabel.value });
});

const canFollow = computed(
  () =>
    authStore.isAuthenticated &&
    object.value !== null &&
    !object.value.unavailable,
);

// Playable queue: the object's own audio (tracks) or its cached children
// carrying audio (album/library/playlist items).
const playableTracks = computed(() =>
  object.value ? remoteObjectPlayableTracks(object.value) : [],
);

const actions = computed(() => [
  {
    key: "play",
    label:
      playableTracks.value.length > 1 ? t("common.playAll") : t("common.play"),
    icon: "play",
    variant: "primary" as const,
    visible: playableTracks.value.length > 0,
  },
  {
    key: "follow",
    label: followLabel.value,
    icon: object.value?.follow_state ? "user-minus" : "user-plus",
    variant: "secondary" as const,
    visible: canFollow.value,
    loading: followBusy.value,
  },
  collectionAction.value,
]);

async function toggleFollow() {
  const target = object.value;
  if (!target || followBusy.value || !canFollow.value) return;
  followBusy.value = true;
  try {
    if (target.follow_state) {
      await unfollowRemoteObject(target.id);
      target.follow_state = null;
      toastStore.push({
        type: "success",
        message: t("remote.unfollowSuccess"),
      });
    } else {
      const result = await followRemoteObject(target.id);
      target.follow_state = result.follow_state ?? "pending";
      toastStore.push({
        type: "success",
        message: t("remote.followSuccess"),
      });
    }
  } catch (err) {
    const message =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
    toastStore.push({
      type: "error",
      message: t("remote.followError", { message }),
    });
  } finally {
    followBusy.value = false;
  }
}

function playTracks(tracks = playableTracks.value) {
  if (!tracks.length) return;
  try {
    player.playAll(tracks);
  } catch (err) {
    toastStore.push({
      type: "error",
      message:
        getApiErrorMessage(err) ||
        t("pages.home.playError", { message: String(err) }),
    });
  }
}

function playItem(item: RemoteObject) {
  const track = remoteObjectToQueueTrack(item);
  if (track) playTracks([track]);
}

async function onAction(key: string) {
  if (key === "follow") await toggleFollow();
  else if (key === "collection") await toggleCollection();
  else if (key === "play") playTracks();
}

function childKind(item: RemoteObject): string {
  return item.resource_type
    ? t(`remote.kinds.${item.resource_type}`, item.resource_type)
    : item.object_type;
}

async function load() {
  const id = String(route.params.id ?? "");
  if (!id || !kind.value) return;
  loading.value = true;
  failed.value = false;
  object.value = null;
  try {
    object.value = await getRemoteResource(kind.value, id);
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

watch(() => [route.params.kind, route.params.id], load, { immediate: true });
</script>

<template>
  <div class="remote-resource">
    <AppPageTitle icon="globe">{{
      t("remote.resourceTitle", { kind: kindLabel })
    }}</AppPageTitle>
    <SkeletonLoader v-if="loading" variant="card" />
    <p
      v-else-if="failed || !object"
      class="remote-resource__error"
      role="alert"
    >
      {{ t("remote.objectUnavailable") }}
    </p>
    <article v-else class="remote-resource__card">
      <img
        v-if="object.image_url"
        :src="object.image_url"
        :alt="object.name || kindLabel"
        class="remote-resource__image"
      />
      <div class="remote-resource__info">
        <p class="remote-resource__remote">
          <span class="remote-resource__badge">
            <AppIcon name="globe" spacing="right" />{{ t("remote.badge") }}
          </span>
          <span class="remote-resource__domain">{{ object.domain }}</span>
        </p>
        <h1 class="remote-resource__name">
          {{ object.name || object.canonical_url }}
        </h1>
        <p v-if="object.unavailable" class="remote-resource__unavailable">
          {{ t("remote.objectUnavailable") }}
        </p>
        <p v-if="summaryHtml" class="remote-resource__summary">
          <RichContent :html="summaryHtml" :instance-domain="instanceDomain" />
        </p>
        <p v-if="object.parent" class="remote-resource__parent">
          {{ t("remote.partOf") }}
          <RouterLink :to="object.parent.url" class="remote-resource__link">
            {{ object.parent.name || object.parent.canonical_url }}
          </RouterLink>
        </p>
        <EntityActions :actions="actions" @select="onAction" />
        <p class="remote-resource__meta">
          <RouterLink
            v-if="object.actor_handle"
            :to="`/@${object.actor_handle}`"
            class="remote-resource__actor"
          >
            @{{ object.actor_handle }}
          </RouterLink>
          <a
            :href="object.canonical_url"
            target="_blank"
            rel="noopener"
            class="remote-resource__origin"
          >
            <AppIcon name="arrow-up-right-from-square" spacing="right" />
            {{ t("remote.viewOriginal", { domain: object.domain }) }}
          </a>
        </p>
      </div>
    </article>
    <section
      v-if="object && object.items && object.items.length"
      class="remote-resource__items"
    >
      <h2 class="remote-resource__items-title">
        {{ t("remote.items", { kind: kindLabel }) }}
      </h2>
      <ul class="remote-resource__items-list">
        <li
          v-for="item in object.items"
          :key="item.id"
          class="remote-resource__item"
        >
          <button
            v-if="item.stream_url || item.audio_url"
            type="button"
            class="remote-resource__item-play"
            :aria-label="t('common.play')"
            :title="t('common.play')"
            @click="playItem(item)"
          >
            <AppIcon name="play" />
          </button>
          <RouterLink :to="item.url" class="remote-resource__link">
            {{ item.name || item.canonical_url }}
          </RouterLink>
          <span class="remote-resource__item-kind">{{ childKind(item) }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.remote-resource {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.remote-resource__error {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}

.remote-resource__card {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  max-width: 60rem;
}

.remote-resource__image {
  width: 10rem;
  height: 10rem;
  object-fit: cover;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.remote-resource__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.remote-resource__remote {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin: 0;
}

.remote-resource__badge {
  font-size: 0.875rem;
  background-color: var(--color-surface-raised);
  color: var(--color-text-secondary);
  padding: calc(1.25 * var(--space-1)) var(--space-2);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.remote-resource__domain {
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.remote-resource__name {
  margin: 0;
  font-size: 1.5rem;
}

.remote-resource__unavailable {
  margin: 0;
  color: var(--color-warning, var(--color-text-muted));
}

.remote-resource__summary {
  margin: 0;
  color: var(--color-text-secondary);
  word-break: break-word;
}

.remote-resource__item-play {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.remote-resource__item-play:hover {
  color: var(--color-text);
  background-color: var(--color-surface-raised);
}

.remote-resource__meta {
  display: flex;
  gap: var(--space-4);
  margin: 0;
}

.remote-resource__actor,
.remote-resource__origin {
  color: var(--color-text-link);
  text-decoration: none;
}

.remote-resource__actor:hover,
.remote-resource__origin:hover {
  text-decoration: underline;
}

.remote-resource__parent {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.remote-resource__items {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 60rem;
}

.remote-resource__items-title {
  margin: 0;
  font-size: 1.125rem;
}

.remote-resource__items-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.remote-resource__item {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.remote-resource__link {
  color: var(--color-text-link);
  text-decoration: none;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.remote-resource__link:hover {
  text-decoration: underline;
}

.remote-resource__item-kind {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  flex-shrink: 0;
}
</style>
