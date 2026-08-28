<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { listHashtags, type HashtagSummary } from "@/api/hashtags";
import { getApiErrorMessage } from "@/api/client";
import { useDebounce } from "@/composables/useDebounce";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import SortControl from "@/components/ui/SortControl.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();

const LIMIT = 48;

const items = ref<HashtagSummary[]>([]);
const total = ref(0);
const offset = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);

const query = ref("");
const sortBy = ref<string>("name");
const sortDir = ref<"asc" | "desc">("asc");

const page = computed(() => Math.floor(offset.value / LIMIT) + 1);

const sortOptions = computed(() => [
  { value: "name", label: t("sort.fields.name") },
  { value: "item_count", label: t("sort.fields.item_count") },
  { value: "first_used", label: t("sort.fields.first_used") },
  { value: "last_used", label: t("sort.fields.last_used") },
]);

async function load() {
  if (loading.value) return;

  loading.value = true;
  error.value = null;

  try {
    const result = await listHashtags({
      q: query.value || undefined,
      limit: LIMIT,
      offset: offset.value,
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
    });
    items.value = result.items;
    total.value = result.total;
    offset.value = result.offset;
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

const search = useDebounce((q: string) => {
  query.value = q;
  offset.value = 0;
  void load();
}, 300);

function onPageChange(nextPage: number) {
  offset.value = (nextPage - 1) * LIMIT;
  void load();
}

function onSort(field: string, direction: "asc" | "desc") {
  sortBy.value = field;
  sortDir.value = direction;
  offset.value = 0;
  void load();
}

function retry() {
  void load();
}

watch([sortBy, sortDir], () => {
  offset.value = 0;
  void load();
});

onMounted(() => load());
</script>

<template>
  <div class="hashtags-view">
    <div class="hashtags-view__header">
      <AppPageTitle icon="hashtag">{{
        t("hashtags.allHashtags")
      }}</AppPageTitle>

      <SearchBar
        :model-value="query"
        :placeholder="t('hashtags.searchPlaceholder')"
        @update:model-value="search"
      />

      <SortControl
        :model-value="sortBy"
        :direction="sortDir"
        :options="sortOptions"
        @update:model-value="(field) => onSort(field, sortDir)"
        @update:direction="(dir) => onSort(sortBy, dir)"
      />
    </div>

    <div v-if="loading && items.length === 0" class="hashtags-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="hashtags-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="items.length === 0"
      class="hashtags-view__empty"
      role="status"
    >
      {{ t("hashtags.empty") }}
    </div>

    <template v-else>
      <ul class="hashtags-view__grid" role="list">
        <li v-for="item in items" :key="item.name" class="hashtags-view__item">
          <RouterLink
            :to="`/hashtags/${encodeURIComponent(item.name)}`"
            class="hashtags-view__card"
          >
            <span class="hashtags-view__name">{{ item.name }}</span>
            <span class="hashtags-view__count">
              {{ t("hashtags.itemCount", { count: item.item_count }) }}
            </span>
          </RouterLink>
        </li>
      </ul>

      <AppPagination
        v-if="total > LIMIT"
        :page="page"
        :total="total"
        :per-page="LIMIT"
        @update:page="onPageChange"
      />
    </template>
  </div>
</template>

<style scoped>
.hashtags-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.hashtags-view__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.hashtags-view__skeleton {
  min-height: 16rem;
}

.hashtags-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.hashtags-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.hashtags-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr));
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.hashtags-view__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  text-align: center;
  transition: background-color var(--transition-fast);
}

.hashtags-view__card:hover {
  background-color: var(--color-surface-hover);
}

.hashtags-view__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hashtags-view__count {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}
</style>
