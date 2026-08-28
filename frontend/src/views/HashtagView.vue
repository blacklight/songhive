<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  listHashtagItems,
  deleteHashtag,
  type TaggedItem,
} from "@/api/hashtags";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import SortControl from "@/components/ui/SortControl.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import TaggedItemCard from "@/components/hashtags/TaggedItemCard.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const authStore = useAuthStore();
const confirm = useConfirmStore();
const toast = useToastStore();

const LIMIT = 24;

const hashtagName = computed(() => String(route.params.name));

const items = ref<TaggedItem[]>([]);
const total = ref(0);
const offset = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const deleting = ref(false);

const sortBy = ref<string>("created_at");
const sortDir = ref<"asc" | "desc">("desc");

const page = computed(() => Math.floor(offset.value / LIMIT) + 1);

const sortOptions = computed(() => [
  { value: "created_at", label: t("sort.fields.created_at") },
  { value: "type", label: t("sort.fields.type") },
]);

async function load() {
  if (loading.value) return;

  loading.value = true;
  error.value = null;

  try {
    const result = await listHashtagItems(hashtagName.value, {
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
    message: t("hashtags.deleteConfirm", { name: hashtagName.value }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  deleting.value = true;
  try {
    await deleteHashtag(hashtagName.value);
    toast.push({
      type: "success",
      message: t("hashtags.deleteSuccess"),
    });
    await router.push("/hashtags");
  } catch (err) {
    toast.push({
      type: "error",
      message: t("hashtags.deleteError", { message: getApiErrorMessage(err) }),
    });
  } finally {
    deleting.value = false;
  }
}

function retry() {
  void load();
}

watch(
  () => route.params.name,
  () => {
    offset.value = 0;
    void load();
  },
);

onMounted(() => load());
</script>

<template>
  <div class="hashtag-view">
    <div class="hashtag-view__header">
      <div class="hashtag-view__title-row">
        <AppPageTitle icon="hashtag" class="hashtag-view__title">
          {{ hashtagName }}
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

      <div class="hashtag-view__controls">
        <SortControl
          :model-value="sortBy"
          :direction="sortDir"
          :options="sortOptions"
          @update:model-value="(field) => onSort(field, sortDir)"
          @update:direction="(dir) => onSort(sortBy, dir)"
        />
      </div>
    </div>

    <div v-if="loading && items.length === 0" class="hashtag-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="hashtag-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="items.length === 0"
      class="hashtag-view__empty"
      role="status"
    >
      {{ t("hashtags.emptyItems") }}
    </div>

    <template v-else>
      <div class="hashtag-view__grid" role="list">
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
.hashtag-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.hashtag-view__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.hashtag-view__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.hashtag-view__title {
  margin: 0;
  font-size: 2rem;
  word-break: break-word;
}

.hashtag-view__controls {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.hashtag-view__skeleton {
  min-height: 16rem;
}

.hashtag-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.hashtag-view__empty {
  text-align: center;
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.hashtag-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: var(--space-4);
}
</style>
