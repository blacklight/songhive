<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";

import {
  searchPreview,
  type SearchEntity,
  type SearchResultItem,
} from "@/api/search";
import {
  SEARCH_ENTITIES,
  useSearchSections,
} from "@/composables/useSearchSections";

import AppButton from "@/components/ui/AppButton.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import SearchBar from "@/components/ui/SearchBar.vue";
import SortControl from "@/components/ui/SortControl.vue";

import AlbumCard from "@/components/library/AlbumCard.vue";
import ArtistCard from "@/components/library/ArtistCard.vue";
import LibraryCard from "@/components/library/LibraryCard.vue";
import PlaylistCard from "@/components/library/PlaylistCard.vue";
import UserLink from "@/components/user/UserLink.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();

const {
  query,
  activeEntities,
  sections,
  entityConfig,
  searchAll,
  searchSection,
  setPage,
  setSort,
  retry,
} = useSearchSections();

const hasSearched = ref(false);

function parseEntitiesParam(value: unknown): SearchEntity[] {
  if (!value) return [];
  const list: string = Array.isArray(value)
    ? String(value[0] ?? "")
    : String(value);
  const parsed = list
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item): item is SearchEntity =>
      (SEARCH_ENTITIES as readonly string[]).includes(item),
    );
  return parsed.length ? parsed : [];
}

function sortOptions(entity: SearchEntity) {
  const config = entityConfig(entity);
  return config.sortable.map((field) => ({
    value: field,
    label: t(`sort.fields.${field}`),
  }));
}

function syncRoute() {
  const trimmed = query.value.trim();
  const queryRecord: Record<string, string | undefined> = {};
  if (trimmed) {
    queryRecord.q = trimmed;
  }
  if (activeEntities.value.length < SEARCH_ENTITIES.length) {
    queryRecord.entities = activeEntities.value.join(",");
  }
  void router.replace({ name: "search", query: queryRecord });
}

async function performSearch() {
  hasSearched.value = true;
  syncRoute();
  await searchAll(query.value, activeEntities.value);
}

function onSearch() {
  performSearch();
}

function toggleEntity(entity: SearchEntity) {
  const index = activeEntities.value.indexOf(entity);
  if (index >= 0) {
    if (activeEntities.value.length === 1) {
      return;
    }
    activeEntities.value.splice(index, 1);
    sections[entity].items = [];
    sections[entity].total = 0;
    sections[entity].offset = 0;
    sections[entity].page = 0;
  } else {
    activeEntities.value.push(entity);
    if (query.value.trim()) {
      void searchSection(entity);
    }
  }
  syncRoute();
}

async function fetchPreview(
  term: string,
  entities: SearchEntity[],
  limit: number,
) {
  const response = await searchPreview(term, entities, limit);
  return response.sections;
}

function onSelectSuggestion(item: SearchResultItem) {
  if (item.url && item.url.startsWith("/")) {
    void router.push(item.url);
  }
}

function onPageChange(entity: SearchEntity, page: number) {
  setPage(entity, page - 1);
  syncRoute();
}

function onSortChange(
  entity: SearchEntity,
  field: string,
  direction: "asc" | "desc",
) {
  setSort(entity, field, direction);
  syncRoute();
}

onMounted(() => {
  const parsed = parseEntitiesParam(route.query.entities);
  activeEntities.value = parsed.length ? parsed : [...SEARCH_ENTITIES];
  query.value = String(route.query.q ?? "");
});

watch(query, () => performSearch());

const showStart = computed(() => !hasSearched.value || !query.value.trim());
</script>

<template>
  <main class="search-view">
    <h1 class="search-view__title">{{ t("search.title") }}</h1>

    <SearchBar
      v-model="query"
      class="search-view__bar"
      :placeholder="t('search.placeholder')"
      :autocomplete="true"
      :autocomplete-entities="activeEntities"
      :autocomplete-fetcher="fetchPreview"
      @search="onSearch"
      @select-suggestion="onSelectSuggestion"
    />

    <div
      class="search-view__filters"
      role="group"
      :aria-label="t('search.filters')"
    >
      <label
        v-for="entity in SEARCH_ENTITIES"
        :key="entity"
        class="search-view__filter"
      >
        <input
          type="checkbox"
          :checked="activeEntities.includes(entity)"
          :disabled="
            activeEntities.length === 1 && activeEntities[0] === entity
          "
          @change="toggleEntity(entity)"
        />
        {{ t(`search.entities.${entity}`) }}
      </label>
    </div>

    <p v-if="showStart" class="search-view__start">
      {{ t("search.start") }}
    </p>

    <div v-else class="search-view__sections">
      <section
        v-for="entity in activeEntities"
        :key="entity"
        class="search-view__section"
      >
        <header class="search-view__section-header">
          <h2 class="search-view__section-title">
            {{ t(`search.entities.${entity}`) }}
            <span class="search-view__section-count">
              ({{ sections[entity].total }})
            </span>
          </h2>
          <SortControl
            :model-value="sections[entity].sortBy"
            :direction="sections[entity].sortDir"
            :options="sortOptions(entity)"
            @update:model-value="
              onSortChange(entity, $event, sections[entity].sortDir)
            "
            @update:direction="
              onSortChange(entity, sections[entity].sortBy, $event)
            "
          />
        </header>

        <div
          v-if="sections[entity].error"
          class="search-view__section-error"
          role="alert"
        >
          {{ sections[entity].error }}
          <AppButton size="sm" @click="retry(entity)">
            {{ t("common.retry") }}
          </AppButton>
        </div>

        <div
          v-else-if="sections[entity].loading && !sections[entity].items.length"
          class="search-view__section-loading"
        >
          {{ t("common.loading") }}
        </div>

        <p
          v-else-if="!sections[entity].items.length"
          class="search-view__section-empty"
        >
          {{ t("search.noResults") }}
        </p>

        <template v-else>
          <ul
            v-if="entity === 'tracks'"
            class="search-view__grid search-view__grid--tracks"
          >
            <li
              v-for="track in sections[entity].items"
              :key="track.id"
              class="search-view__item search-view__item--track"
            >
              <img
                v-if="track.image_url"
                :src="track.image_url"
                :alt="track.title"
                class="search-view__thumb"
              />
              <span
                v-else
                class="search-view__thumb search-view__thumb--empty"
              />
              <RouterLink :to="`/tracks/${track.id}`" class="search-view__link">
                {{ track.title }}
              </RouterLink>
              <span
                v-if="track.artist || track.album"
                class="search-view__meta"
              >
                {{
                  [track.artist?.name, track.album?.title]
                    .filter(Boolean)
                    .join(" · ")
                }}
              </span>
            </li>
          </ul>

          <ul
            v-else-if="entity === 'albums'"
            class="search-view__grid search-view__grid--cards"
          >
            <li
              v-for="album in sections[entity].items"
              :key="album.id"
              class="search-view__card"
            >
              <AlbumCard :album="album" />
            </li>
          </ul>

          <ul
            v-else-if="entity === 'artists'"
            class="search-view__grid search-view__grid--cards"
          >
            <li
              v-for="artist in sections[entity].items"
              :key="artist.id"
              class="search-view__card"
            >
              <ArtistCard :artist="artist" />
            </li>
          </ul>

          <ul
            v-else-if="entity === 'playlists'"
            class="search-view__grid search-view__grid--cards"
          >
            <li
              v-for="playlist in sections[entity].items"
              :key="playlist.id"
              class="search-view__card"
            >
              <PlaylistCard :playlist="playlist" />
            </li>
          </ul>

          <ul
            v-else-if="entity === 'libraries'"
            class="search-view__grid search-view__grid--cards"
          >
            <li
              v-for="library in sections[entity].items"
              :key="library.id"
              class="search-view__card"
            >
              <LibraryCard :library="library" />
            </li>
          </ul>

          <ul
            v-else-if="entity === 'users'"
            class="search-view__grid search-view__grid--users"
          >
            <li
              v-for="user in sections[entity].items"
              :key="user.username"
              class="search-view__item search-view__item--user"
            >
              <UserLink
                :username="user.username"
                :display-name="user.display_name"
                :avatar-url="user.avatar_url"
                size="md"
              />
              <span
                v-if="user.bio"
                class="search-view__meta search-view__meta--bio"
              >
                {{ user.bio }}
              </span>
            </li>
          </ul>

          <ul
            v-else-if="entity === 'tags' || entity === 'genres'"
            class="search-view__grid search-view__grid--tags"
          >
            <li
              v-for="item in sections[entity].items"
              :key="item.name"
              class="search-view__tag"
            >
              <RouterLink
                :to="`/${entity}/${item.name}`"
                class="search-view__tag-link"
              >
                {{ item.name }}
              </RouterLink>
              <span class="search-view__tag-count">
                {{ t(`${entity}.itemCount`, { count: item.item_count }) }}
              </span>
            </li>
          </ul>
        </template>

        <AppPagination
          v-if="sections[entity].total > sections[entity].limit"
          class="search-view__pagination"
          :page="sections[entity].page + 1"
          :total="sections[entity].total"
          :per-page="sections[entity].limit"
          @update:page="onPageChange(entity, $event)"
        />
      </section>
    </div>
  </main>
</template>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.search-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.search-view__bar {
  width: 100%;
  max-width: 48rem;
}

.search-view__filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.search-view__filter {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
}

.search-view__start,
.search-view__section-empty {
  color: var(--color-text-muted);
}

.search-view__sections {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.search-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.search-view__section-header {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
  justify-content: space-between;
}

.search-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.search-view__section-count {
  font-weight: normal;
  color: var(--color-text-muted);
}

.search-view__section-error {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  color: var(--color-danger);
}

.search-view__section-loading {
  color: var(--color-text-muted);
}

.search-view__grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--space-4);
}

.search-view__grid--cards {
  grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
}

.search-view__grid--tracks,
.search-view__grid--users,
.search-view__grid--tags {
  grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr));
}

.search-view__item--track,
.search-view__item--user,
.search-view__tag {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.search-view__thumb {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: var(--radius-sm);
  object-fit: cover;
  background-color: var(--color-surface-raised);
}

.search-view__thumb--empty {
  display: inline-block;
}

.search-view__link,
.search-view__tag-link {
  color: var(--color-text);
  text-decoration: none;
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-view__link:hover,
.search-view__tag-link:hover {
  text-decoration: underline;
}

.search-view__meta {
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-view__meta--bio {
  flex: 1 1 auto;
}

.search-view__tag-count {
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
}
</style>
