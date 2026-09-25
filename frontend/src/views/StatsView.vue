<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { getApiErrorMessage } from "@/api/client";
import {
  getClockStats,
  getGenresTimeline,
  getPlaysStats,
  getReleasesStats,
  getTopStats,
  type StatsBuckets,
  type StatsClock,
  type StatsGenreBuckets,
  type StatsPeriodGroupBy,
  type StatsReleaseGroupBy,
  type StatsReleases,
  type StatsTop,
  type StatsTopEntry,
} from "@/api/stats";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppTable, { type Column } from "@/components/ui/AppTable.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import GenreTimeline from "@/components/stats/GenreTimeline.vue";
import ListeningClock from "@/components/stats/ListeningClock.vue";
import StatsHistogram from "@/components/stats/StatsHistogram.vue";
import StatsRangePicker from "@/components/stats/StatsRangePicker.vue";
import TopEntityCell from "@/components/stats/TopEntityCell.vue";

const { t, locale } = useI18n();

// Calendar buckets are built server-side in this timezone.
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

// First weekday of the configured locale. `Intl` reports 1 = Monday …
// 7 = Sunday; the API takes Python's 0 = Monday … 6 = Sunday.
const weekStart = computed(() => {
  try {
    const info = (
      new Intl.Locale(locale.value) as Intl.Locale & {
        getWeekInfo?: () => { firstDay: number };
      }
    ).getWeekInfo?.();
    return info ? (info.firstDay - 1) % 7 : 0;
  } catch {
    return 0;
  }
});

function toDateInput(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

const fromDate = ref(toDateInput(new Date(Date.now() - 30 * 86400000)));
const toDate = ref(toDateInput(new Date()));

const playsGroupBy = ref<StatsPeriodGroupBy>("day");
const genresGroupBy = ref<StatsPeriodGroupBy>("week");
const releasesGroupBy = ref<StatsReleaseGroupBy>("decade");

const top = ref<StatsTop | null>(null);
const plays = ref<StatsBuckets | null>(null);
const genres = ref<StatsGenreBuckets | null>(null);
const releases = ref<StatsReleases | null>(null);
const clock = ref<StatsClock | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const period = computed(() => ({
  // Date inputs carry local calendar days; translate them into exact
  // instants so the period covers the user's days regardless of timezone.
  from: new Date(`${fromDate.value}T00:00:00`).toISOString(),
  to: new Date(`${toDate.value}T23:59:59.999`).toISOString(),
  tz,
  weekStart: weekStart.value,
}));

// Bucket labels arrive as ISO dates (day/week buckets carry the bucket's
// first day), year-months, plain years, or release labels like "1990s".
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_MONTH = /^(\d{4})-(\d{2})$/;

function formatBucket(bucket: string): string {
  const day = ISO_DAY.exec(bucket);
  if (day) {
    return new Intl.DateTimeFormat(locale.value, {
      dateStyle: "medium",
    }).format(new Date(+day[1], +day[2] - 1, +day[3]));
  }
  const month = ISO_MONTH.exec(bucket);
  if (month) {
    return new Intl.DateTimeFormat(locale.value, {
      year: "numeric",
      month: "short",
    }).format(new Date(+month[1], +month[2] - 1, 1));
  }
  return bucket;
}

function formatBuckets<T extends { bucket: string }>(
  buckets: T[] | null,
): T[] | undefined {
  return buckets?.map((entry) => ({
    ...entry,
    bucket: formatBucket(entry.bucket),
  }));
}

const playsBuckets = computed(() => formatBuckets(plays.value));
const genresBuckets = computed(() => formatBuckets(genres.value));
const releasesBuckets = computed(() => formatBuckets(releases.value));

function errorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function refreshPlays() {
  plays.value = await getPlaysStats(period.value, playsGroupBy.value);
}

async function refreshGenres() {
  genres.value = await getGenresTimeline(period.value, genresGroupBy.value);
}

async function refreshReleases() {
  releases.value = await getReleasesStats(period.value, releasesGroupBy.value);
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    [top.value, plays.value, genres.value, releases.value, clock.value] =
      await Promise.all([
        getTopStats(period.value),
        getPlaysStats(period.value, playsGroupBy.value),
        getGenresTimeline(period.value, genresGroupBy.value),
        getReleasesStats(period.value, releasesGroupBy.value),
        getClockStats(period.value),
      ]);
  } catch (err) {
    error.value = errorMessage(err);
  } finally {
    loading.value = false;
  }
}

async function guardRefresh(refresh: () => Promise<void>) {
  error.value = null;
  try {
    await refresh();
  } catch (err) {
    error.value = errorMessage(err);
  }
}

const isEmpty = computed(
  () =>
    top.value !== null &&
    top.value.artists.length === 0 &&
    top.value.albums.length === 0 &&
    top.value.tracks.length === 0 &&
    top.value.genres.length === 0,
);

const periodGroupBys: StatsPeriodGroupBy[] = ["day", "week", "month", "year"];
const releaseGroupBys: StatsReleaseGroupBy[] = ["decade", "year"];

const topColumns = computed<Column[]>(() => [
  { key: "rank", label: "#", align: "right" },
  { key: "name", label: t("pages.stats.name"), align: "left" },
  { key: "play_count", label: t("pages.stats.playsCount"), align: "right" },
]);

function topRows(entries?: StatsTop["tracks"]): Record<string, unknown>[] {
  return (entries ?? []).map((entry, index) => ({
    rank: index + 1,
    ...entry,
  }));
}

function asTopEntry(row: Record<string, unknown>): StatsTopEntry {
  return row as StatsTopEntry;
}

watch([fromDate, toDate], () => load());
watch(playsGroupBy, () => guardRefresh(refreshPlays));
watch(genresGroupBy, () => guardRefresh(refreshGenres));
watch(releasesGroupBy, () => guardRefresh(refreshReleases));

onMounted(() => load());
</script>

<template>
  <div class="stats-view">
    <AppPageTitle class="stats-view__title" icon="chart-column">
      {{ t("pages.stats.title") }}
    </AppPageTitle>

    <StatsRangePicker
      v-model:from="fromDate"
      v-model:to="toDate"
      :from-label="t('pages.stats.from')"
      :to-label="t('pages.stats.to')"
    />

    <div v-if="error" class="stats-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div v-else-if="loading && top === null" class="stats-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="isEmpty" class="stats-view__empty" role="status">
      {{ t("pages.stats.empty") }}
    </div>

    <template v-else>
      <div class="stats-view__top-grid">
        <section class="stats-view__card">
          <h2>{{ t("pages.stats.topArtists") }}</h2>
          <AppTable
            :columns="topColumns"
            :rows="topRows(top?.artists)"
            :loading="loading"
            :empty-label="t('pages.stats.emptySection')"
          >
            <template #row-name="{ row }">
              <TopEntityCell :entry="asTopEntry(row)" icon="microphone" />
            </template>
          </AppTable>
        </section>
        <section class="stats-view__card">
          <h2>{{ t("pages.stats.topAlbums") }}</h2>
          <AppTable
            :columns="topColumns"
            :rows="topRows(top?.albums)"
            :loading="loading"
            :empty-label="t('pages.stats.emptySection')"
          >
            <template #row-name="{ row }">
              <TopEntityCell :entry="asTopEntry(row)" icon="compact-disc" />
            </template>
          </AppTable>
        </section>
        <section class="stats-view__card">
          <h2>{{ t("pages.stats.topTracks") }}</h2>
          <AppTable
            :columns="topColumns"
            :rows="topRows(top?.tracks)"
            :loading="loading"
            :empty-label="t('pages.stats.emptySection')"
          >
            <template #row-name="{ row }">
              <TopEntityCell :entry="asTopEntry(row)" icon="music" />
            </template>
          </AppTable>
        </section>
        <section class="stats-view__card">
          <h2>{{ t("pages.stats.topGenres") }}</h2>
          <AppTable
            :columns="topColumns"
            :rows="topRows(top?.genres)"
            :loading="loading"
            :empty-label="t('pages.stats.emptySection')"
          >
            <template #row-name="{ row }">
              <TopEntityCell :entry="asTopEntry(row)" icon="tags" />
            </template>
          </AppTable>
        </section>
      </div>

      <section class="stats-view__card">
        <div class="stats-view__card-header">
          <h2>{{ t("pages.stats.plays") }}</h2>
          <div class="stats-view__segment" role="group">
            <AppButton
              v-for="group in periodGroupBys"
              :key="group"
              size="sm"
              :variant="playsGroupBy === group ? 'primary' : 'secondary'"
              @click="playsGroupBy = group"
            >
              {{ t(`pages.stats.groupBy.${group}`) }}
            </AppButton>
          </div>
        </div>
        <StatsHistogram v-if="playsBuckets" :buckets="playsBuckets" />
      </section>

      <section class="stats-view__card">
        <div class="stats-view__card-header">
          <h2>{{ t("pages.stats.genresTimeline") }}</h2>
          <div class="stats-view__segment" role="group">
            <AppButton
              v-for="group in periodGroupBys"
              :key="group"
              size="sm"
              :variant="genresGroupBy === group ? 'primary' : 'secondary'"
              @click="genresGroupBy = group"
            >
              {{ t(`pages.stats.groupBy.${group}`) }}
            </AppButton>
          </div>
        </div>
        <GenreTimeline v-if="genresBuckets" :buckets="genresBuckets" />
      </section>

      <section class="stats-view__card">
        <div class="stats-view__card-header">
          <h2>{{ t("pages.stats.releases") }}</h2>
          <div class="stats-view__segment" role="group">
            <AppButton
              v-for="group in releaseGroupBys"
              :key="group"
              size="sm"
              :variant="releasesGroupBy === group ? 'primary' : 'secondary'"
              @click="releasesGroupBy = group"
            >
              {{ t(`pages.stats.groupBy.${group}`) }}
            </AppButton>
          </div>
        </div>
        <StatsHistogram v-if="releasesBuckets" :buckets="releasesBuckets" />
      </section>

      <section class="stats-view__card stats-view__clock">
        <h2>{{ t("pages.stats.clock") }}</h2>
        <ListeningClock v-if="clock" :hours="clock" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.stats-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.stats-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.stats-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.stats-view__skeleton {
  min-height: 16rem;
}

.stats-view__empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}

.stats-view__top-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(36rem, 1fr));
  gap: var(--space-4);
}

/* Fixed layout keeps the numeric columns at their declared width so the
   "Plays" header and counts never wrap; the name column clips instead. */
.stats-view__card :deep(.app-table) {
  table-layout: fixed;
}

.stats-view__card :deep(.app-table__cell--rank) {
  width: 2.5rem;
  white-space: nowrap;
}

.stats-view__card :deep(.app-table__cell--name) {
  overflow: hidden;
}

.stats-view__card :deep(.app-table__cell--play_count) {
  width: 4.5rem;
  white-space: nowrap;
}

@media (max-width: 767px) {
  .stats-view__top-grid {
    grid-template-columns: 1fr;
  }

  .stats-view__card :deep(.app-table th),
  .stats-view__card :deep(.app-table td) {
    padding: var(--space-2);
  }

  .stats-view__card :deep(.app-table__cell--rank) {
    display: none;
  }

  .stats-view__card :deep(.app-table__cell--play_count) {
    width: 3.5rem;
  }
}

.stats-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  min-width: 0;
}

.stats-view__card h2 {
  margin: 0;
  font-size: 1rem;
}

.stats-view__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.stats-view__segment {
  display: inline-flex;
  gap: var(--space-1);
}

.stats-view__clock {
  max-width: 36rem;
}
</style>
