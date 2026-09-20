<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  getPodcast,
  listEpisodes,
  markEpisodePlayed,
  markEpisodeUnplayed,
  refreshPodcast,
  unfollowPodcast,
  episodeToQueueTrack,
  type PodcastEpisodeResponse,
  type PodcastResponse,
} from "@/api/podcasts";
import { getApiErrorMessage } from "@/api/client";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { formatTime } from "@/utils/time";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const player = usePlayerStore();
const toast = useToastStore();

const podcastId = computed(() => String(route.params.id));

const podcast = ref<PodcastResponse | null>(null);
const episodes = ref<PodcastEpisodeResponse[]>([]);
const loading = ref(false);
const loadingEpisodes = ref(false);
const error = ref<string | null>(null);
const refreshing = ref(false);
const unfollowing = ref(false);
const hasMore = ref(false);
const episodeLimit = 50;

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    podcast.value = await getPodcast(podcastId.value);
  } catch (err) {
    error.value = t("pages.podcast.loadError", {
      message: getErrorMessage(err),
    });
    return;
  } finally {
    loading.value = false;
  }
  await loadEpisodes(true);
}

async function loadEpisodes(reset = false) {
  if (loadingEpisodes.value) return;
  const offset = reset ? 0 : episodes.value.length;
  loadingEpisodes.value = true;
  try {
    const result = await listEpisodes(podcastId.value, {
      limit: episodeLimit,
      offset,
    });
    episodes.value = reset ? result : [...episodes.value, ...result];
    hasMore.value = result.length === episodeLimit;
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcast.episodesError", {
        message: getErrorMessage(err),
      }),
    });
    hasMore.value = false;
  } finally {
    loadingEpisodes.value = false;
  }
}

async function onRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    podcast.value = await refreshPodcast(podcastId.value);
    toast.push({ type: "success", message: t("pages.podcast.refreshSuccess") });
    await loadEpisodes(true);
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcast.refreshError", {
        message: getErrorMessage(err),
      }),
    });
  } finally {
    refreshing.value = false;
  }
}

async function onUnfollow() {
  if (unfollowing.value) return;
  unfollowing.value = true;
  try {
    await unfollowPodcast(podcastId.value);
    toast.push({
      type: "success",
      message: t("pages.podcast.unfollowSuccess"),
    });
    await router.push({ name: "podcasts" });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcast.unfollowError", {
        message: getErrorMessage(err),
      }),
    });
  } finally {
    unfollowing.value = false;
  }
}

function onPlay(episode: PodcastEpisodeResponse) {
  const show = podcast.value;
  if (!show) return;
  const queue = episodes.value.map((item) => episodeToQueueTrack(item, show));
  const index = episodes.value.findIndex((item) => item.id === episode.id);
  player.playAll(queue, Math.max(index, 0));
}

function onPlayAll() {
  const show = podcast.value;
  if (!show || episodes.value.length === 0) return;
  player.playAll(episodes.value.map((item) => episodeToQueueTrack(item, show)));
}

const togglingPlayed = ref<Set<string>>(new Set());

async function onTogglePlayed(episode: PodcastEpisodeResponse) {
  if (togglingPlayed.value.has(episode.id)) return;
  togglingPlayed.value.add(episode.id);
  const target = !episode.played;
  try {
    if (target) {
      await markEpisodePlayed(episode.id);
    } else {
      await markEpisodeUnplayed(episode.id);
    }
    episode.played = target;
    if (podcast.value) {
      podcast.value.unplayed_count = Math.max(
        0,
        podcast.value.unplayed_count + (target ? -1 : 1),
      );
    }
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcast.playedError", {
        message: getErrorMessage(err),
      }),
    });
  } finally {
    togglingPlayed.value.delete(episode.id);
  }
}

onMounted(load);
</script>

<template>
  <div class="podcast-view">
    <div v-if="loading" class="podcast-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="podcast-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else-if="podcast">
      <header class="podcast-view__header">
        <img
          v-if="podcast.image_url"
          :src="podcast.image_url"
          :alt="podcast.title"
          class="podcast-view__cover"
        />
        <span v-else class="podcast-view__cover podcast-view__cover--empty">
          <AppIcon name="podcast" />
        </span>

        <div class="podcast-view__info">
          <AppPageTitle class="podcast-view__title" icon="podcast">
            {{ podcast.title }}
          </AppPageTitle>
          <p v-if="podcast.author" class="podcast-view__author">
            {{ podcast.author }}
          </p>
          <p class="podcast-view__meta">
            {{
              t("pages.podcasts.episodeCount", { count: podcast.episode_count })
            }}
            <template v-if="podcast.language">
              · {{ podcast.language }}</template
            >
            <template v-if="podcast.explicit">
              · {{ t("pages.podcast.explicit") }}</template
            >
          </p>
          <p v-if="podcast.categories.length" class="podcast-view__categories">
            <span
              v-for="category in podcast.categories"
              :key="category"
              class="podcast-view__category"
              >{{ category }}</span
            >
          </p>
          <p v-if="podcast.description" class="podcast-view__description">
            {{ podcast.description }}
          </p>
          <p v-if="podcast.link" class="podcast-view__link">
            <a :href="podcast.link" target="_blank" rel="noopener noreferrer">
              <AppIcon name="globe" spacing="right" />{{ podcast.link }}
            </a>
          </p>
          <p
            v-if="podcast.last_error"
            class="podcast-view__feed-error"
            role="status"
          >
            <AppIcon name="triangle-exclamation" spacing="right" />
            {{
              t("pages.podcast.feedErrorDetail", {
                message: podcast.last_error,
              })
            }}
          </p>
        </div>
      </header>

      <div class="podcast-view__actions">
        <AppButton icon="play" @click="onPlayAll">
          {{ t("common.playAll") }}
        </AppButton>
        <AppButton
          variant="secondary"
          icon="rotate"
          :loading="refreshing"
          @click="onRefresh"
        >
          {{ t("pages.podcast.refresh") }}
        </AppButton>
        <AppButton
          v-if="podcast.following"
          variant="secondary"
          icon="xmark"
          :loading="unfollowing"
          @click="onUnfollow"
        >
          {{ t("pages.podcast.unfollow") }}
        </AppButton>
      </div>

      <section
        class="podcast-view__episodes"
        :aria-label="t('pages.podcast.episodes')"
      >
        <p
          v-if="!loadingEpisodes && episodes.length === 0"
          class="podcast-view__empty"
        >
          {{ t("pages.podcast.emptyEpisodes") }}
        </p>

        <ul v-else class="podcast-view__episode-list" role="list">
          <li
            v-for="episode in episodes"
            :key="episode.id"
            class="podcast-view__episode"
            :class="{ 'podcast-view__episode--played': episode.played }"
          >
            <AppButton
              variant="ghost"
              size="sm"
              icon="play"
              class="podcast-view__episode-play"
              :aria-label="t('common.play')"
              @click="onPlay(episode)"
            />
            <img
              v-if="episode.image_url || podcast.image_url"
              :src="episode.image_url || podcast.image_url || ''"
              :alt="episode.title"
              class="podcast-view__episode-cover"
              loading="lazy"
            />
            <div class="podcast-view__episode-info">
              <span class="podcast-view__episode-title">
                <span
                  v-if="episode.episode_type === 'trailer'"
                  class="podcast-view__episode-kind"
                  >{{ t("pages.podcast.trailer") }}</span
                >
                {{ episode.title }}
              </span>
              <span class="podcast-view__episode-meta">
                <template v-if="episode.published_at">
                  {{ formatDate(episode.published_at) }}
                </template>
                <template v-if="episode.duration_seconds">
                  · {{ formatTime(episode.duration_seconds) }}
                </template>
                <template v-if="episode.season_number">
                  ·
                  {{ t("pages.podcast.season", { n: episode.season_number }) }}
                </template>
              </span>
              <p
                v-if="episode.description"
                class="podcast-view__episode-description"
              >
                {{ episode.description }}
              </p>
            </div>
            <AppButton
              variant="ghost"
              size="sm"
              :icon="episode.played ? 'circle-check' : 'check'"
              class="podcast-view__episode-played"
              :title="
                episode.played
                  ? t('pages.podcast.markUnplayed')
                  : t('pages.podcast.markPlayed')
              "
              @click="onTogglePlayed(episode)"
            />
          </li>
        </ul>

        <div v-if="hasMore" class="podcast-view__footer">
          <AppButton
            icon="chevron-down"
            variant="secondary"
            :loading="loadingEpisodes"
            @click="loadEpisodes()"
          >
            {{ t("browse.list.loadMore") }}
          </AppButton>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.podcast-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.podcast-view__skeleton {
  min-height: 16rem;
}

.podcast-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.podcast-view__header {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
}

.podcast-view__cover {
  width: 10rem;
  height: 10rem;
  flex-shrink: 0;
  border-radius: var(--radius-md);
  object-fit: cover;
  background-color: var(--color-surface-raised);
}

.podcast-view__cover--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 3rem;
}

.podcast-view__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.podcast-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.podcast-view__author {
  margin: 0;
  font-size: 1rem;
  color: var(--color-text-muted);
}

.podcast-view__meta {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.podcast-view__categories {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.podcast-view__category {
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-full, 999px);
  background-color: var(--color-surface);
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.podcast-view__description {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-view__link a {
  color: var(--color-text-link);
  text-decoration: none;
  word-break: break-all;
}

.podcast-view__link a:hover {
  text-decoration: underline;
}

.podcast-view__feed-error {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-warning);
}

.podcast-view__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.podcast-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.podcast-view__episode-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.podcast-view__episode {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.podcast-view__episode-play {
  flex-shrink: 0;
  margin-top: var(--space-1);
}

.podcast-view__episode-played {
  flex-shrink: 0;
  margin-top: var(--space-1);
  margin-left: auto;
  color: var(--color-text-muted);
}

.podcast-view__episode--played .podcast-view__episode-title,
.podcast-view__episode--played .podcast-view__episode-description {
  color: var(--color-text-muted);
}

.podcast-view__episode--played .podcast-view__episode-played {
  color: var(--color-accent);
}

.podcast-view__episode-cover {
  width: 3rem;
  height: 3rem;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  object-fit: cover;
  background-color: var(--color-surface-raised);
}

.podcast-view__episode-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.podcast-view__episode-title {
  font-weight: 600;
}

.podcast-view__episode-kind {
  margin-right: var(--space-1);
  padding: 0 var(--space-1);
  border-radius: var(--radius-sm);
  background-color: var(--color-surface-raised);
  font-size: 0.75rem;
  font-weight: 500;
  text-transform: uppercase;
}

.podcast-view__episode-meta {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
}

.podcast-view__episode-description {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-view__footer {
  display: flex;
  justify-content: center;
  padding-top: var(--space-2);
}

@media (max-width: 640px) {
  .podcast-view__header {
    flex-direction: column;
  }

  .podcast-view__cover {
    width: 6rem;
    height: 6rem;
  }
}
</style>
