<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  listPodcasts,
  followPodcast,
  importOpml,
  opmlExportUrl,
  type PodcastResponse,
} from "@/api/podcasts";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const toast = useToastStore();

const podcasts = ref<PodcastResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const limit = 50;
const hasMore = ref(false);

const feedUrl = ref("");
const following = ref(false);
const followError = ref<string | null>(null);

const opmlInput = ref<HTMLInputElement | null>(null);
const importing = ref(false);

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function load(reset = false) {
  if (loading.value) return;

  const offset = reset ? 0 : podcasts.value.length;
  loading.value = true;
  if (reset) error.value = null;

  try {
    const result = await listPodcasts({ limit, offset });
    podcasts.value = reset ? result : [...podcasts.value, ...result];
    hasMore.value = result.length === limit;
  } catch (err) {
    error.value = t("pages.podcasts.loadError", {
      message: getErrorMessage(err),
    });
    hasMore.value = false;
  } finally {
    loading.value = false;
  }
}

async function onFollow() {
  const url = feedUrl.value.trim();
  if (!url || following.value) return;

  following.value = true;
  followError.value = null;
  try {
    const podcast = await followPodcast(url);
    feedUrl.value = "";
    toast.push({
      type: "success",
      message: t("pages.podcasts.followSuccess", { title: podcast.title }),
    });
    podcasts.value = [
      podcast,
      ...podcasts.value.filter((p) => p.id !== podcast.id),
    ];
  } catch (err) {
    followError.value = t("pages.podcasts.followError", {
      message: getErrorMessage(err),
    });
  } finally {
    following.value = false;
  }
}

function onImportClick() {
  opmlInput.value?.click();
}

async function onOpmlFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || importing.value) return;

  importing.value = true;
  try {
    const result = await importOpml(file);
    toast.push({
      type: result.failed > 0 ? "warning" : "success",
      message: t("pages.podcasts.importResult", {
        subscribed: result.subscribed,
        skipped: result.skipped,
        failed: result.failed,
      }),
    });
    for (const message of result.errors.slice(0, 3)) {
      toast.push({ type: "error", message });
    }
    await load(true);
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.podcasts.importError", {
        message: getErrorMessage(err),
      }),
    });
  } finally {
    importing.value = false;
  }
}

void load(true);
</script>

<template>
  <div class="podcasts-view">
    <AppPageTitle class="podcasts-view__title" icon="podcast">
      {{ t("pages.podcasts.title") }}
    </AppPageTitle>

    <form class="podcasts-view__follow" @submit.prevent="onFollow">
      <AppInput
        v-model="feedUrl"
        type="url"
        class="podcasts-view__follow-input"
        :label="t('pages.podcasts.feedUrl')"
        :placeholder="t('pages.podcasts.feedUrlPlaceholder')"
        :required="true"
      />
      <AppButton type="submit" icon="plus" :loading="following">
        {{ t("pages.podcasts.follow") }}
      </AppButton>
    </form>
    <p v-if="followError" class="podcasts-view__follow-error" role="alert">
      {{ followError }}
    </p>

    <div class="podcasts-view__opml">
      <AppButton
        variant="secondary"
        size="sm"
        icon="file-import"
        :loading="importing"
        @click="onImportClick"
      >
        {{ t("pages.podcasts.importOpml") }}
      </AppButton>
      <a
        class="podcasts-view__opml-export"
        :href="opmlExportUrl()"
        download="podcasts.opml"
      >
        <AppIcon name="file-export" />
        {{ t("pages.podcasts.exportOpml") }}
      </a>
      <input
        ref="opmlInput"
        type="file"
        accept=".opml,.xml,text/xml,text/x-opml"
        class="podcasts-view__opml-input"
        @change="onOpmlFileChange"
      />
    </div>

    <div v-if="error" class="podcasts-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load(true)">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="loading && podcasts.length === 0"
      class="podcasts-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="podcasts.length === 0" class="podcasts-view__empty">
      {{ t("pages.podcasts.empty") }}
    </div>

    <ul v-else class="podcasts-view__list" role="list">
      <li v-for="podcast in podcasts" :key="podcast.id">
        <RouterLink
          :to="{ name: 'podcast', params: { id: podcast.id } }"
          class="podcasts-view__podcast"
        >
          <img
            v-if="podcast.image_url"
            :src="podcast.image_url"
            :alt="podcast.title"
            class="podcasts-view__cover"
            loading="lazy"
          />
          <span v-else class="podcasts-view__cover podcasts-view__cover--empty">
            <AppIcon name="podcast" />
          </span>
          <span class="podcasts-view__info">
            <span class="podcasts-view__name">{{ podcast.title }}</span>
            <span v-if="podcast.author" class="podcasts-view__author">
              {{ podcast.author }}
            </span>
            <span class="podcasts-view__meta">
              {{
                t("pages.podcasts.episodeCount", {
                  count: podcast.episode_count,
                })
              }}
            </span>
            <span
              v-if="podcast.last_error"
              class="podcasts-view__feed-error"
              :title="podcast.last_error"
            >
              <AppIcon name="triangle-exclamation" />
              {{ t("pages.podcasts.feedError") }}
            </span>
          </span>
        </RouterLink>
      </li>
    </ul>

    <div v-if="!error && hasMore" class="podcasts-view__footer">
      <AppButton
        icon="chevron-down"
        variant="secondary"
        :loading="loading"
        @click="load()"
      >
        {{ t("browse.list.loadMore") }}
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.podcasts-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.podcasts-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.podcasts-view__follow {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.podcasts-view__follow-input {
  flex: 1;
  min-width: 0;
}

.podcasts-view__follow-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.9375rem;
}

.podcasts-view__opml {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.podcasts-view__opml-export {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: 0.9375rem;
  text-decoration: none;
}

.podcasts-view__opml-export:hover {
  text-decoration: underline;
}

.podcasts-view__opml-input {
  display: none;
}

.podcasts-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.podcasts-view__skeleton {
  min-height: 16rem;
}

.podcasts-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.podcasts-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr));
  gap: var(--space-3);
}

.podcasts-view__podcast {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.podcasts-view__podcast:hover {
  background-color: var(--color-surface-hover);
}

.podcasts-view__cover {
  width: 4rem;
  height: 4rem;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  object-fit: cover;
  background-color: var(--color-surface-raised);
}

.podcasts-view__cover--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  font-size: 1.5rem;
}

.podcasts-view__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.podcasts-view__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.podcasts-view__author,
.podcasts-view__meta {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.podcasts-view__feed-error {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 0.8125rem;
  color: var(--color-warning);
}

.podcasts-view__footer {
  display: flex;
  justify-content: center;
}
</style>
