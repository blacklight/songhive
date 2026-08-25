<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { listHistory, type HistoryPage } from "@/api/history";
import { getTrack } from "@/api/tracks";
import { getApiErrorMessage } from "@/api/client";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { toQueueTrack } from "@/player/enrich";
import type { QueueTrack } from "@/player/types";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppTable, { type Column } from "@/components/ui/AppTable.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import { formatDateTime } from "@/i18n";

type HistoryEntry = HistoryPage["items"][number];

const { t } = useI18n();
const player = usePlayerStore();
const toast = useToastStore();

const items = ref<HistoryEntry[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const page = ref(1);
const pageSize = 20;
const query = ref("");

async function load(nextPage = 1, clearQuery = true) {
  // The backend list endpoint has no search param, so the client-side filter
  // only applies to entries already on the current page. Clear the query on
  // page changes to avoid misleading partial matches, but preserve it on retry
  // so the user does not lose their search text when re-fetching fails.
  if (clearQuery) {
    query.value = "";
  }

  loading.value = true;
  error.value = null;

  try {
    const response = await listHistory({ page: nextPage, pageSize });
    items.value = response.items;
    page.value = response.page;
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

const hasMore = computed(() => items.value.length === pageSize);

const filteredItems = computed<HistoryEntry[]>(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return items.value;

  return items.value.filter((entry) => {
    const title = (entry.title ?? "").toLowerCase();
    const artist = (entry.artist ?? "").toLowerCase();
    return title.includes(q) || artist.includes(q);
  });
});

const emptyLabel = computed(() =>
  query.value.trim()
    ? t("pages.history.emptySearch")
    : t("pages.history.empty"),
);

function onPrevious() {
  if (page.value > 1) load(page.value - 1);
}

function onNext() {
  if (hasMore.value) load(page.value + 1);
}

async function onPlayAgain(entry: HistoryEntry) {
  try {
    const track = await getTrack(entry.track_id);
    player.playTrack(toQueueTrack(track, { artist_name: entry.artist ?? "" }));
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.history.playError", {
        message:
          getApiErrorMessage(err) ||
          (err instanceof Error ? err.message : t("errors.unknown")),
      }),
    });
  }
}

async function onPlayAll() {
  const visible = filteredItems.value;
  if (visible.length === 0) return;

  const resolved = await Promise.all(
    visible.map((entry) =>
      getTrack(entry.track_id)
        .then((track) =>
          toQueueTrack(track, { artist_name: entry.artist ?? "" }),
        )
        .catch(() => null),
    ),
  );

  const tracks = resolved.filter(
    (track): track is QueueTrack => track !== null,
  );
  if (tracks.length === 0) {
    toast.push({
      type: "error",
      message: t("pages.history.playError", {
        message: t("errors.unknown"),
      }),
    });
    return;
  }

  if (tracks.length < visible.length) {
    toast.push({
      type: "warning",
      message: t("pages.history.playAllPartial", {
        count: tracks.length,
        total: visible.length,
      }),
    });
  }

  player.playAll(tracks);
}

interface HistoryRow {
  id: string;
  title: string;
  artist: string;
  playedAt: string;
  entry: HistoryEntry;
}

const columns: Column[] = [
  { key: "title", label: t("browse.entities.track"), align: "left" },
  { key: "artist", label: t("browse.entities.artist"), align: "left" },
  { key: "playedAt", label: t("pages.history.playedAt"), align: "left" },
  { key: "actions", label: t("browse.detail.actions"), align: "center" },
];

const rows = computed<Record<string, unknown>[]>(() =>
  filteredItems.value.map((entry) => ({
    id: entry.id,
    title: entry.title ?? t("pages.history.untitled"),
    artist: entry.artist ?? "—",
    playedAt: formatDateTime(entry.created_at),
    entry,
  })),
);

function asRow(row: Record<string, unknown>): HistoryRow {
  return row as unknown as HistoryRow;
}

function rowKey(row: Record<string, unknown>, index: number): string {
  return String(row.id ?? `row-${index}`);
}

onMounted(() => load());
</script>

<template>
  <div class="history-view">
    <AppPageTitle class="history-view__title" icon="clock-rotate-left">{{
      t("pages.history.title")
    }}</AppPageTitle>

    <div class="history-view__controls">
      <AppInput
        v-model="query"
        type="search"
        class="history-view__search"
        :placeholder="t('pages.history.searchPlaceholder')"
      />
      <AppButton
        size="sm"
        icon="play"
        :disabled="filteredItems.length === 0"
        @click="onPlayAll"
      >
        {{ t("browse.detail.playAll") }}
      </AppButton>
    </div>

    <div v-if="error" class="history-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load(page, false)">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="loading && items.length === 0"
      class="history-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <AppTable
      v-else
      :columns="columns"
      :rows="rows"
      :row-key="rowKey"
      :loading="loading"
      :empty-label="emptyLabel"
    >
      <template #row-actions="{ row }">
        <AppButton
          size="sm"
          icon="rotate-right"
          :aria-label="t('pages.history.playAgain')"
          @click="onPlayAgain(asRow(row).entry)"
        >
          {{ t("pages.history.playAgain") }}
        </AppButton>
      </template>
    </AppTable>

    <div v-if="!error" class="history-view__pagination">
      <AppButton
        size="sm"
        icon="chevron-left"
        variant="secondary"
        :disabled="page === 1 || loading"
        @click="onPrevious"
      >
        {{ t("pages.history.previous") }}
      </AppButton>
      <AppButton
        size="sm"
        icon="chevron-right"
        variant="secondary"
        :disabled="!hasMore || loading"
        @click="onNext"
      >
        {{ t("pages.history.next") }}
      </AppButton>
    </div>
  </div>
</template>

<style scoped>
.history-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.history-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.history-view__controls {
  display: flex;
  gap: var(--space-3);
  align-items: end;
  flex-wrap: wrap;
}

.history-view__search {
  flex: 1;
  min-width: 16rem;
  max-width: 32rem;
}

.history-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.history-view__skeleton {
  min-height: 16rem;
}

.history-view__pagination {
  display: flex;
  justify-content: center;
  gap: var(--space-3);
}
</style>
