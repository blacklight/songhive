import { ref, reactive } from "vue";

import { listAlbumsWithMeta } from "@/api/albums";
import { listArtistsWithMeta } from "@/api/artists";
import { listGenres } from "@/api/genres";
import { listLibrariesWithMeta } from "@/api/libraries";
import { listPlaylistsWithMeta } from "@/api/playlists";
import { searchPreview, type SearchEntity } from "@/api/search";
import { listTags } from "@/api/tags";
import { listTracksWithMeta } from "@/api/tracks";
import { listPublicUsers } from "@/api/users";

export interface SearchSectionConfig {
  entity: SearchEntity;
  labelKey: string;
  sortable: string[];
  defaultSortBy: string;
  defaultSortDir: "asc" | "desc";
  include?: string;
}

export interface SearchSectionState {
  items: any[];
  total: number;
  offset: number;
  loading: boolean;
  error: string | null;
  sortBy: string;
  sortDir: "asc" | "desc";
  limit: number;
  page: number;
}

export interface SearchSectionResult<T = unknown> {
  items: T[];
  total: number;
  offset: number;
}

type SearchFetcher = (
  params: Record<string, string | number | boolean | undefined | null>,
) => Promise<SearchSectionResult>;

const SECTION_REGISTRY: Record<
  SearchEntity,
  { config: SearchSectionConfig; fetch: SearchFetcher }
> = {
  tracks: {
    config: {
      entity: "tracks",
      labelKey: "search.entities.tracks",
      sortable: [
        "title",
        "artist_name",
        "album_title",
        "created_at",
        "updated_at",
        "release_year",
      ],
      defaultSortBy: "title",
      defaultSortDir: "asc",
      include: "artist,album",
    },
    fetch: async (params) => {
      const result = await listTracksWithMeta({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        include: params.include as string | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.tracks,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  albums: {
    config: {
      entity: "albums",
      labelKey: "search.entities.albums",
      sortable: [
        "title",
        "artist_name",
        "created_at",
        "updated_at",
        "release_year",
      ],
      defaultSortBy: "title",
      defaultSortDir: "asc",
      include: "artist",
    },
    fetch: async (params) => {
      const result = await listAlbumsWithMeta({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        include: params.include as string | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.items,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  artists: {
    config: {
      entity: "artists",
      labelKey: "search.entities.artists",
      sortable: ["name", "created_at", "updated_at"],
      defaultSortBy: "name",
      defaultSortDir: "asc",
    },
    fetch: async (params) => {
      const result = await listArtistsWithMeta({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.items,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  playlists: {
    config: {
      entity: "playlists",
      labelKey: "search.entities.playlists",
      sortable: ["name", "created_at", "updated_at"],
      defaultSortBy: "name",
      defaultSortDir: "asc",
    },
    fetch: async (params) => {
      const result = await listPlaylistsWithMeta({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        include: params.include as string | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.items,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  libraries: {
    config: {
      entity: "libraries",
      labelKey: "search.entities.libraries",
      sortable: ["name", "created_at", "updated_at"],
      defaultSortBy: "name",
      defaultSortDir: "asc",
    },
    fetch: async (params) => {
      const result = await listLibrariesWithMeta({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        include: params.include as string | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.items,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  users: {
    config: {
      entity: "users",
      labelKey: "search.entities.users",
      sortable: ["username", "created_at"],
      defaultSortBy: "username",
      defaultSortDir: "asc",
    },
    fetch: async (params) => {
      const result = await listPublicUsers({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.users,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  tags: {
    config: {
      entity: "tags",
      labelKey: "search.entities.tags",
      sortable: ["name", "item_count", "first_used", "last_used"],
      defaultSortBy: "name",
      defaultSortDir: "asc",
    },
    fetch: async (params) => {
      const result = await listTags({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.items,
        total: result.total,
        offset: result.offset,
      };
    },
  },
  genres: {
    config: {
      entity: "genres",
      labelKey: "search.entities.genres",
      sortable: ["name", "item_count", "first_used", "last_used"],
      defaultSortBy: "name",
      defaultSortDir: "asc",
    },
    fetch: async (params) => {
      const result = await listGenres({
        q: params.q as string | undefined,
        limit: params.limit as number | undefined,
        offset: params.offset as number | undefined,
        sort_by: params.sort_by as string | undefined,
        sort_dir: params.sort_dir as "asc" | "desc" | undefined,
      });
      return {
        items: result.items,
        total: result.total,
        offset: result.offset,
      };
    },
  },
};

export const SEARCH_ENTITIES: SearchEntity[] = [
  "tracks",
  "albums",
  "artists",
  "playlists",
  "libraries",
  "users",
  "tags",
  "genres",
];

export function useSearchSections() {
  const query = ref("");
  const activeEntities = ref<SearchEntity[]>([...SEARCH_ENTITIES]);
  const sections = reactive<Record<SearchEntity, SearchSectionState>>(
    Object.fromEntries(
      SEARCH_ENTITIES.map((entity) => [
        entity,
        {
          items: [],
          total: 0,
          offset: 0,
          loading: false,
          error: null,
          sortBy: SECTION_REGISTRY[entity].config.defaultSortBy,
          sortDir: SECTION_REGISTRY[entity].config.defaultSortDir,
          limit: 10,
          page: 0,
        },
      ]),
    ) as unknown as Record<SearchEntity, SearchSectionState>,
  );

  function entityConfig(entity: SearchEntity) {
    return SECTION_REGISTRY[entity].config;
  }

  function resetSection(entity: SearchEntity) {
    const config = entityConfig(entity);
    const section = sections[entity];
    section.items = [];
    section.total = 0;
    section.offset = 0;
    section.loading = false;
    section.error = null;
    section.sortBy = config.defaultSortBy;
    section.sortDir = config.defaultSortDir;
    section.page = 0;
  }

  async function searchSection(entity: SearchEntity) {
    if (!query.value.trim() || !activeEntities.value.includes(entity)) {
      resetSection(entity);
      return;
    }
    const config = entityConfig(entity);
    const section = sections[entity];
    section.loading = true;
    section.error = null;
    try {
      const result = await SECTION_REGISTRY[entity].fetch({
        q: query.value,
        limit: section.limit,
        offset: section.offset,
        include: config.include,
        sort_by: section.sortBy,
        sort_dir: section.sortDir,
      });
      section.items = result.items;
      section.total = result.total;
      section.offset = result.offset;
    } catch (err) {
      section.error = err instanceof Error ? err.message : String(err);
    } finally {
      section.loading = false;
    }
  }

  async function searchAll(
    q: string,
    active: SearchEntity[] = activeEntities.value,
  ) {
    query.value = q;
    activeEntities.value = active;
    const tasks = SEARCH_ENTITIES.map((entity) => {
      resetSection(entity);
      return active.includes(entity)
        ? searchSection(entity)
        : Promise.resolve();
    });
    await Promise.all(tasks);
  }

  function setPage(entity: SearchEntity, page: number) {
    const section = sections[entity];
    section.page = page;
    section.offset = page * section.limit;
    return searchSection(entity);
  }

  function setSort(
    entity: SearchEntity,
    sortBy: string,
    sortDir: "asc" | "desc",
  ) {
    const config = entityConfig(entity);
    if (!config.sortable.includes(sortBy)) {
      return Promise.resolve();
    }
    const section = sections[entity];
    section.sortBy = sortBy;
    section.sortDir = sortDir;
    section.offset = 0;
    section.page = 0;
    return searchSection(entity);
  }

  function retry(entity: SearchEntity) {
    return searchSection(entity);
  }

  async function preview(
    term: string,
    entities: SearchEntity[] = activeEntities.value,
    limit = 5,
  ) {
    const response = await searchPreview(term, entities, limit);
    return response;
  }

  return {
    query,
    activeEntities,
    sections,
    entityConfig,
    resetSection,
    searchAll,
    searchSection,
    setPage,
    setSort,
    retry,
    preview,
  };
}
