<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import type { StreamResponse } from "@/api/streams";
import type { ActivityAttachment } from "@/api/activities";
import ActivityAudioPlayer from "@/components/activities/ActivityAudioPlayer.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppIcon from "@/components/ui/AppIcon.vue";

interface Props {
  stream: StreamResponse;
  // The owner line is redundant where the listing is already scoped to one
  // user (e.g. a profile tab).
  showOwner?: boolean;
}

const props = withDefaults(defineProps<Props>(), { showOwner: true });
const { t } = useI18n();

function ownerName(): string {
  return props.stream.owner.display_name || props.stream.owner.username;
}

function ownerRoute(): string {
  return `/@${props.stream.owner.username}`;
}

function statusLabel(): string {
  if (!props.stream.enabled) return t("pages.streams.disabled");
  return props.stream.online
    ? t("pages.streams.live")
    : t("pages.streams.offline");
}

function formatLabel(): string {
  return [
    props.stream.format,
    props.stream.bitrate ? `${props.stream.bitrate} kbps` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function nowPlayingLabel(): string {
  const np = props.stream.now_playing;
  if (!np) return "";
  return [np.artist, np.title].filter(Boolean).join(" - ");
}

function nowPlayingRoute(): string | null {
  const id = props.stream.now_playing?.track_id;
  return id ? `/tracks/${id}` : null;
}

// The embedded player is the same one used for audio attachments in
// federated posts: a synthesized Audio attachment pointing at the
// mountpoint URL, flagged live so it lazy-connects and skips seek/download.
const attachment = computed<ActivityAttachment>(() => ({
  type: "Audio",
  mediaType: props.stream.content_type ?? undefined,
  url: props.stream.stream_url,
  name: props.stream.name,
  "songhive:trackTitle": props.stream.name,
  id: `stream:${props.stream.id}`,
}));
</script>

<template>
  <li class="stream-card">
    <div class="stream-card__header">
      <RouterLink
        v-if="showOwner"
        :to="ownerRoute()"
        class="stream-card__owner"
        :title="ownerName()"
      >
        <AppAvatar
          :src="stream.owner.avatar_url || ''"
          :name="ownerName()"
          size="sm"
        />
        <span class="stream-card__owner-name">{{ ownerName() }}</span>
      </RouterLink>
      <div class="stream-card__badges">
        <span
          v-if="stream.visibility === 'private'"
          class="stream-card__badge stream-card__badge--private"
        >
          <AppIcon name="lock" />
          {{ t("pages.streams.private") }}
        </span>
        <span
          class="stream-card__badge"
          :class="{
            'stream-card__badge--live': stream.online,
            'stream-card__badge--disabled': !stream.enabled,
          }"
        >
          <AppIcon :name="stream.online ? 'circle' : 'circle-stop'" />
          {{ statusLabel() }}
        </span>
      </div>
    </div>

    <div class="stream-card__body">
      <div class="stream-card__meta">
        <h2 class="stream-card__name">{{ stream.name }}</h2>
        <p class="stream-card__details">
          <a :href="stream.stream_url" target="_blank" rel="noopener">
            <code class="stream-card__mount">/streams/{{ stream.mount }}</code>
          </a>
          <span v-if="formatLabel()">{{ formatLabel() }}</span>
          <span v-if="stream.genre">{{ stream.genre }}</span>
          <span v-if="stream.listener_count > 0" class="stream-card__listeners">
            <AppIcon name="headphones" />
            {{ t("pages.streams.listeners", stream.listener_count) }}
          </span>
        </p>
        <p v-if="stream.description" class="stream-card__description">
          {{ stream.description }}
        </p>
        <p
          v-if="stream.online && stream.now_playing"
          class="stream-card__now-playing"
        >
          <AppIcon name="music" />
          {{ t("pages.streams.nowPlaying") }}:
          <RouterLink
            v-if="nowPlayingRoute()"
            :to="nowPlayingRoute()!"
            class="stream-card__now-playing-link"
          >
            {{ nowPlayingLabel() }}
          </RouterLink>
          <span v-else>{{ nowPlayingLabel() }}</span>
        </p>
      </div>
      <a
        :href="stream.stream_url"
        target="_blank"
        rel="noopener"
        class="stream-card__open"
        :title="t('pages.streams.openStream')"
        :aria-label="t('pages.streams.openStream')"
      >
        <AppIcon name="arrow-up-right-from-square" />
      </a>
    </div>

    <ActivityAudioPlayer
      :attachment="attachment"
      :avatar-url="stream.owner.avatar_url || undefined"
      live
    />
  </li>
</template>

<style scoped>
.stream-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background-color: var(--color-surface);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-md);
}

.stream-card:hover {
  border: 1px solid var(--color-surface-raised);
}

.stream-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.stream-card__owner {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  color: var(--color-text-muted);
  text-decoration: none;
  font-size: 0.875rem;
}

.stream-card__owner:hover {
  color: var(--color-text-hover);
}

.stream-card__owner-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stream-card__badges {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  margin-left: auto;
}

.stream-card__badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  border-radius: var(--radius-full, 999px);
  border: 1px solid var(--color-border);
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.stream-card__badge--live {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.stream-card__badge--private {
  color: var(--color-text);
}

.stream-card__body {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.stream-card__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  flex: 1;
}

.stream-card__name {
  margin: 0;
  font-size: 1.125rem;
}

.stream-card__details {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.stream-card__mount {
  font-size: 0.8125rem;
}

.stream-card__listeners {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.stream-card__description {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
}

.stream-card__now-playing {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: 0.9375rem;
}

.stream-card__now-playing-link {
  color: var(--color-text-link);
  text-decoration: none;
}

.stream-card__now-playing-link:hover {
  text-decoration: underline;
}

.stream-card__open {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-2);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.stream-card__open:hover {
  background-color: var(--color-surface-hover);
  color: var(--color-text-hover);
}
</style>
