<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listStreams,
  type StreamResponse,
  type StreamUpdateEvent,
} from "@/api/streams";
import { eventBus, type WsEvent } from "@/api/ws";
import { useAuthStore } from "@/stores/auth";
import { useStreamActions } from "@/composables/useStreamActions";
import { getApiErrorMessage } from "@/api/client";
import StreamCard from "@/components/streams/StreamCard.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const auth = useAuthStore();
const { busyStreams, toggleEnabled, togglePlayback } = useStreamActions();

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

const hasStreams = computed(() => streams.value.length > 0);

function onStreamUpdate(event: WsEvent) {
  const data = event.data as StreamUpdateEvent | undefined;
  if (!data || typeof data.mount !== "string") return;
  const stream = streams.value.find((s) => s.mount === data.mount);
  if (!stream) return;
  if (typeof data.online === "boolean") stream.online = data.online;
  if (typeof data.live === "boolean") stream.live = data.live;
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
      <StreamCard
        v-for="stream in streams"
        :key="stream.id"
        :stream="stream"
        :busy="busyStreams.has(stream.id)"
        @toggle-enabled="toggleEnabled"
        @toggle-playback="togglePlayback"
      />
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
</style>
