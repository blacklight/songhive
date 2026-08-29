<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import AppTabs from "@/components/ui/AppTabs.vue";
import SortControl from "@/components/ui/SortControl.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TaggedItemCard from "@/components/hashtags/TaggedItemCard.vue";

export interface Item {
  type: string;
  id: string;
}

export interface ListParams {
  limit?: number;
  offset?: number;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  type?: string;
}

export interface ListResult {
  items: Item[];
  total: number;
  offset: number;
}

export type ItemKind = "hashtag" | "genre";

export interface Props {
  kind: ItemKind;
  name: string;
  availableTypes: string[];
  loadItems: (name: string, params: ListParams) => Promise<ListResult>;
  deleteItem: (name: string) => Promise<unknown>;
}

const props = defineProps<Props>();

const { t } = useI18n();
const router = useRouter();
const authStore = useAuthStore();
const confirm = useConfirmStore();
const toast = useToastStore();

const LIMIT = 24;

const items = ref<Item[]>([]);
const total = ref(0);
const offset = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const deleting = ref(false);

const sortBy = ref<string>("created_at");
const sortDir = ref<"asc" | "desc">("desc");
const activeType = ref<string>("");
const visibleTypes = ref<string[]>([]);

const page = computed(() => Math.floor(offset.value / LIMIT) + 1);

const icon = computed(() => (props.kind === "hashtag" ? "hashtag" : "tag"));

const entityPluralKeys: Record<string, string> = {
  artist: "browse.entities.artists",
  album: "browse.entities.albums",
  track: "browse.entities.tracks",
  playlist: "browse.entities.playlists",
  library: "browse.entities.libraries",
};

const tabs = computed(() =>
  visibleTypes.value.map((type) => ({
    value: type,
    label: t(entityPluralKeys[type] ?? "browse.entities.item"),
  })),
);

const sortOptions = computed(() => [
  { value: "created_at", label: t("sort.fields.created_at") },
]);

async function fetchItems() {
  error.value = null;

  try {
    const result = await props.loadItems(props.name, {
      limit: LIMIT,
      offset: offset.value,
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
      type: activeType.value,
    });
    items.value = result.items;
    total.value = result.total;
    offset.value = result.offset;
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  }
}

async function load() {
  if (loading.value || !activeType.value) return;

  loading.value = true;
  await fetchItems();
  loading.value = false;
}

async function loadVisibleTypes() {
  if (loading.value) return;

  loading.value = true;
  error.value = null;
  items.value = [];
  total.value = 0;
  offset.value = 0;

  try {
    const counts = await Promise.all(
      props.availableTypes.map(async (type) => {
        const result = await props.loadItems(props.name, {
          limit: 1,
          offset: 0,
          sort_by: "created_at",
          sort_dir: "desc",
          type,
        });
        return { type, total: result.total };
      }),
    );

    visibleTypes.value = counts
      .filter(({ total }) => total > 0)
      .map(({ type }) => type);

    if (visibleTypes.value.length === 0) {
      loading.value = false;
      return;
    }

    activeType.value = visibleTypes.value[0];
    sortBy.value = "created_at";
    sortDir.value = "desc";
    await fetchItems();
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

function onTabChange(type: string) {
  activeType.value = type;
  offset.value = 0;
  sortBy.value = "created_at";
  sortDir.value = "desc";
  void load();
}

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

async function onDelete() {
  if (!authStore.isAdmin) return;

  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t(`${props.kind}s.deleteConfirm`, { name: props.name }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  deleting.value = true;
  try {
    await props.deleteItem(props.name);
    toast.push({
      type: "success",
      message: t(`${props.kind}s.deleteSuccess`),
    });
    await router.push(`/${props.kind}s`);
  } catch (err) {
    toast.push({
      type: "error",
      message: t(`${props.kind}s.deleteError`, {
        message: getApiErrorMessage(err),
      }),
    });
  } finally {
    deleting.value = false;
  }
}

function retry() {
  if (visibleTypes.value.length === 0) {
    void loadVisibleTypes();
  } else {
    void load();
  }
}

watch(
  () => props.name,
  () => {
    visibleTypes.value = [];
    activeType.value = "";
    sortBy.value = "created_at";
    sortDir.value = "desc";
    void loadVisibleTypes();
  },
);

onMounted(() => loadVisibleTypes());
</script>

<template>
  <div class="tag-detail-view">
    <div class="tag-detail-view__header">
      <div class="tag-detail-view__title-row">
        <AppPageTitle :icon="icon" class="tag-detail-view__title">
          {{ name }}
        </AppPageTitle>

        <AppButton
          v-if="authStore.isAdmin"
          variant="danger"
          size="sm"
          icon="trash"
          :loading="deleting"
          @click="onDelete"
        >
          {{ t("common.delete") }}
        </AppButton>
      </div>

      <div class="tag-detail-view__controls">
        <AppTabs
          :model-value="activeType"
          :tabs="tabs"
          @update:model-value="onTabChange"
        />

        <SortControl
          :model-value="sortBy"
          :direction="sortDir"
          :options="sortOptions"
          :show-field="false"
          @update:model-value="(field) => onSort(field, sortDir)"
          @update:direction="(dir) => onSort(sortBy, dir)"
        />
      </div>
    </div>

    <div v-if="loading && items.length === 0" class="tag-detail-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="tag-detail-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="items.length === 0"
      class="tag-detail-view__empty"
      role="status"
    >
      {{ t(`${kind}s.emptyItems`) }}
    </div>

    <template v-else>
      <div class="tag-detail-view__grid" role="list">
        <TaggedItemCard
          v-for="item in items"
          :id="item.id"
          :key="`${item.type}:${item.id}`"
          :type="item.type"
        />
      </div>

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
.tag-detail-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.tag-detail-view__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.tag-detail-view__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.tag-detail-view__title {
  margin: 0;
  font-size: 2rem;
  word-break: break-word;
}

.tag-detail-view__controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.tag-detail-view__skeleton {
  min-height: 16rem;
}

.tag-detail-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.tag-detail-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.tag-detail-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}
</style>
