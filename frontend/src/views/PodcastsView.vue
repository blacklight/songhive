<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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
import { useEntityList } from "@/composables/useEntityList";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import SortControl from "@/components/ui/SortControl.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const toast = useToastStore();

const {
  items: podcasts,
  loading,
  error,
  query,
  hasMore,
  sortBy,
  sortDir,
  load,
  loadMore,
  search,
  setSort,
  retry,
  refresh,
} = useEntityList<PodcastResponse>((params) => listPodcasts(params), {
  defaultSortBy: "latest",
  defaultSortDir: "desc",
  syncQuery: true,
});

const feedUrl = ref("");
const following = ref(false);
const followError = ref<string | null>(null);

const opmlInput = ref<HTMLInputElement | null>(null);
const importing = ref(false);

const sortOptions = computed(() => [
  { value: "latest", label: t("pages.podcasts.sort.latest") },
  { value: "unplayed", label: t("pages.podcasts.sort.unplayed") },
  { value: "episodes", label: t("pages.podcasts.sort.episodes") },
  { value: "name", label: t("sort.fields.name") },
]);

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

function onSearch(q: string) {
  void search(q);
}

function onSort(field: string, direction: "asc" | "desc") {
  // Switching fields restores that field's natural direction; toggling the
  // direction on the current field just inverts it.
  const dir =
    field === sortBy.value ? direction : field === "name" ? "asc" : "desc";
  void setSort(field, dir);
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
    await refresh();
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
    await refresh();
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

onMounted(() => load(true));
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

    <div class="podcasts-view__controls">
      <SearchBar
        :model-value="query"
        :debounce="0"
        class="podcasts-view__search"
        :placeholder="t('pages.podcasts.searchPlaceholder')"
        @update:model-value="onSearch"
      />
      <SortControl
        :model-value="sortBy"
        :direction="sortDir"
        :options="sortOptions"
        @update:model-value="(field) => onSort(field, sortDir)"
        @update:direction="(dir) => onSort(sortBy, dir)"
      />
    </div>

    <div v-if="error" class="podcasts-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
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
      {{
        query
          ? t("pages.podcasts.noResults", { query })
          : t("pages.podcasts.empty")
      }}
    </div>

    <ul v-else class="podcasts-view__list" role="list">
      <li v-for="podcast in podcasts" :key="podcast.id">
        <RouterLink
          :to="{ name: 'podcast', params: { id: podcast.id } }"
          class="podcasts-view__podcast"
          :title="
            podcast.latest_episode_at
              ? t('pages.podcasts.latestEpisodeAt', {
                  date: formatDate(podcast.latest_episode_at),
                })
              : undefined
          "
        >
          <span class="podcasts-view__cover-wrap">
            <img
              v-if="podcast.image_url"
              :src="podcast.image_url"
              :alt="podcast.title"
              class="podcasts-view__cover"
              loading="lazy"
            />
            <span
              v-else
              class="podcasts-view__cover podcasts-view__cover--empty"
            >
              <AppIcon name="podcast" />
            </span>
            <span
              v-if="podcast.unplayed_count > 0"
              class="podcasts-view__unplayed"
              :title="
                t('pages.podcasts.unplayedCount', {
                  count: podcast.unplayed_count,
                })
              "
            >
              {{ podcast.unplayed_count }}
            </span>
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
          <span v-if="podcast.latest_episode_at" class="podcasts-view__latest">
            {{
              t("pages.podcasts.latestEpisodeAt", {
                date: formatDate(podcast.latest_episode_at),
              })
            }}
          </span>
        </RouterLink>
      </li>
    </ul>

    <div v-if="!error && hasMore" class="podcasts-view__footer">
      <AppButton
        icon="chevron-down"
        variant="secondary"
        :loading="loading"
        @click="loadMore"
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

.podcasts-view__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.podcasts-view__search {
  flex: 1;
  min-width: 12rem;
  max-width: 32rem;
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
  position: relative;
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

.podcasts-view__cover-wrap {
  position: relative;
  flex-shrink: 0;
}

.podcasts-view__cover {
  width: 4rem;
  height: 4rem;
  display: block;
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

.podcasts-view__unplayed {
  position: absolute;
  top: -0.375rem;
  right: -0.375rem;
  min-width: 1.375rem;
  height: 1.375rem;
  padding: 0 var(--space-1);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-full);
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
  font-size: 0.75rem;
  font-weight: 600;
  line-height: 1;
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

.podcasts-view__latest {
  position: absolute;
  right: var(--space-3);
  bottom: var(--space-2);
  background: var(--color-bg);
  color: var(--color-text-muted);
  font-size: 0.75rem;
  padding: 0 var(--space-2);
  opacity: 0;
  transition: opacity var(--transition-fast);
  border-radius: var(--radius-md);
  pointer-events: none;
}

.podcasts-view__podcast:hover .podcasts-view__latest,
.podcasts-view__podcast:focus-visible .podcasts-view__latest {
  opacity: 1;
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
