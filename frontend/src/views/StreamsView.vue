<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  listStreams,
  type StreamResponse,
  type StreamUpdateEvent,
} from "@/api/streams";
import { eventBus, type WsEvent } from "@/api/ws";
import { useAuthStore } from "@/stores/auth";
import type { ActivityAttachment } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import ActivityAudioPlayer from "@/components/activities/ActivityAudioPlayer.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const auth = useAuthStore();

const streams = ref<StreamResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

// The ``stream_update`` WebSocket event delivers track/online changes
// instantly, but only for authenticated users — the WS endpoint requires a
// session. The poll is both the anonymous users' update path and the
// reconciliation net for whatever events cannot cover (a driver crash
// expires the mount's meta key without an event; listener counts and
// directory membership only change on a reload anyway).
const WS_EVENT = "stream_update";
const POLL_INTERVAL_MS = 30_000;
let pollTimer: ReturnType<typeof setInterval> | null = null;

async function load() {
  if (loading.value) return;
  loading.value = true;
  error.value = null;
  try {
    streams.value = await listStreams();
  } catch (err) {
    error.value = t("pages.streams.loadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    loading.value = false;
  }
}

function ownerName(stream: StreamResponse): string {
  return stream.owner.display_name || stream.owner.username;
}

function ownerRoute(stream: StreamResponse): string {
  return `/@${stream.owner.username}`;
}

function statusLabel(stream: StreamResponse): string {
  if (!stream.enabled) return t("pages.streams.disabled");
  return stream.online ? t("pages.streams.live") : t("pages.streams.offline");
}

function formatLabel(stream: StreamResponse): string {
  return [stream.format, `${stream.bitrate} kbps`].filter(Boolean).join(" · ");
}

function nowPlayingLabel(stream: StreamResponse): string {
  const np = stream.now_playing;
  if (!np) return "";
  return [np.artist, np.title].filter(Boolean).join(" - ");
}

function nowPlayingRoute(stream: StreamResponse): string | null {
  const id = stream.now_playing?.track_id;
  return id ? `/tracks/${id}` : null;
}

// The embedded player is the same one used for audio attachments in
// federated posts: a synthesized Audio attachment pointing at the
// mountpoint URL, flagged live so it lazy-connects and skips seek/download.
function attachmentFor(stream: StreamResponse): ActivityAttachment {
  return {
    type: "Audio",
    mediaType: stream.content_type ?? undefined,
    url: stream.stream_url,
    name: stream.name,
    "songhive:trackTitle": stream.name,
    id: `stream:${stream.id}`,
  };
}

const hasStreams = computed(() => streams.value.length > 0);

function onStreamUpdate(event: WsEvent) {
  const data = event.data as StreamUpdateEvent | undefined;
  if (!data || typeof data.mount !== "string") return;
  const stream = streams.value.find((s) => s.mount === data.mount);
  if (!stream) return;
  if (typeof data.online === "boolean") stream.online = data.online;
  if (data.now_playing !== undefined) {
    stream.now_playing = data.now_playing ?? null;
  }
}

// Silent re-fetch for the poll: transient failures keep the last good
// listing instead of flashing the error banner over live data.
async function refresh() {
  try {
    streams.value = await listStreams();
    error.value = null;
  } catch {
    // The next poll tick retries.
  }
}

onMounted(() => {
  void load();
  pollTimer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
  if (auth.isAuthenticated) {
    eventBus.on(WS_EVENT, onStreamUpdate);
    eventBus.connect();
  }
});

onUnmounted(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  eventBus.off(WS_EVENT, onStreamUpdate);
});
</script>

<template>
  <div class="streams-view">
    <AppPageTitle class="streams-view__title" icon="satellite-dish">{{
      t("pages.streams.title")
    }}</AppPageTitle>

    <div v-if="error" class="streams-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="loading && streams.length === 0"
      class="streams-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="!hasStreams" class="streams-view__empty">
      {{ t("pages.streams.empty") }}
    </div>

    <ul v-else class="streams-view__list" role="list">
      <li v-for="stream in streams" :key="stream.id" class="streams-view__card">
        <div class="streams-view__card-header">
          <RouterLink
            :to="ownerRoute(stream)"
            class="streams-view__owner"
            :title="ownerName(stream)"
          >
            <AppAvatar
              :src="stream.owner.avatar_url || ''"
              :name="ownerName(stream)"
              size="sm"
            />
            <span class="streams-view__owner-name">{{
              ownerName(stream)
            }}</span>
          </RouterLink>
          <div class="streams-view__badges">
            <span
              v-if="stream.visibility === 'private'"
              class="streams-view__badge streams-view__badge--private"
            >
              <AppIcon name="lock" />
              {{ t("pages.streams.private") }}
            </span>
            <span
              class="streams-view__badge"
              :class="{
                'streams-view__badge--live': stream.online,
                'streams-view__badge--disabled': !stream.enabled,
              }"
            >
              <AppIcon :name="stream.online ? 'circle' : 'circle-stop'" />
              {{ statusLabel(stream) }}
            </span>
          </div>
        </div>

        <div class="streams-view__card-body">
          <div class="streams-view__meta">
            <h2 class="streams-view__name">{{ stream.name }}</h2>
            <p class="streams-view__details">
              <a :href="stream.stream_url" target="_blank" rel="noopener">
                <code class="streams-view__mount"
                  >/streams/{{ stream.mount }}</code
                >
              </a>
              <span v-if="formatLabel(stream)">{{ formatLabel(stream) }}</span>
              <span v-if="stream.genre">{{ stream.genre }}</span>
              <span
                v-if="stream.listener_count > 0"
                class="streams-view__listeners"
              >
                <AppIcon name="headphones" />
                {{ t("pages.streams.listeners", stream.listener_count) }}
              </span>
            </p>
            <p v-if="stream.description" class="streams-view__description">
              {{ stream.description }}
            </p>
            <p
              v-if="stream.online && stream.now_playing"
              class="streams-view__now-playing"
            >
              <AppIcon name="music" />
              {{ t("pages.streams.nowPlaying") }}:
              <RouterLink
                v-if="nowPlayingRoute(stream)"
                :to="nowPlayingRoute(stream)!"
                class="streams-view__now-playing-link"
              >
                {{ nowPlayingLabel(stream) }}
              </RouterLink>
              <span v-else>{{ nowPlayingLabel(stream) }}</span>
            </p>
          </div>
          <a
            :href="stream.stream_url"
            target="_blank"
            rel="noopener"
            class="streams-view__open"
            :title="t('pages.streams.openStream')"
            :aria-label="t('pages.streams.openStream')"
          >
            <AppIcon name="arrow-up-right-from-square" />
          </a>
        </div>

        <ActivityAudioPlayer
          :attachment="attachmentFor(stream)"
          :avatar-url="stream.owner.avatar_url || undefined"
          live
        />
      </li>
    </ul>
  </div>
</template>

<style scoped>
.streams-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.streams-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.streams-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.streams-view__skeleton {
  min-height: 16rem;
}

.streams-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.streams-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.streams-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.streams-view__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.streams-view__owner {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  color: var(--color-text-muted);
  text-decoration: none;
  font-size: 0.875rem;
}

.streams-view__owner:hover {
  color: var(--color-text-hover);
}

.streams-view__owner-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.streams-view__badges {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.streams-view__badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  border-radius: var(--radius-full, 999px);
  border: 1px solid var(--color-border);
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.streams-view__badge--live {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.streams-view__badge--private {
  color: var(--color-text);
}

.streams-view__card-body {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.streams-view__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  flex: 1;
}

.streams-view__name {
  margin: 0;
  font-size: 1.125rem;
}

.streams-view__details {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.streams-view__mount {
  font-size: 0.8125rem;
}

.streams-view__listeners {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.streams-view__description {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
}

.streams-view__now-playing {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: 0.9375rem;
}

.streams-view__now-playing-link {
  color: var(--color-text-link);
  text-decoration: none;
}

.streams-view__now-playing-link:hover {
  text-decoration: underline;
}

.streams-view__open {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-2);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.streams-view__open:hover {
  background-color: var(--color-surface-hover);
  color: var(--color-text-hover);
}
</style>
