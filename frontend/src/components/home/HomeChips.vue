<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

export interface HomeChip {
  name: string;
  count?: number;
}

/**
 * A row of genre/tag chips linking to their browse pages. Like
 * ``HomeShelf`` the section removes itself once it has loaded with no
 * items.
 */
export interface Props {
  title: string;
  items: HomeChip[];
  /** Route base path each chip links to, e.g. ``/genres`` or ``/tags``. */
  basePath: string;
  /** Route path of the "see all" link; omitted renders no link. */
  seeAllTo?: string;
  loading?: boolean;
  error?: string | null;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
});

const emit = defineEmits<{ retry: [] }>();
const { t } = useI18n();

const visible = computed(
  () => props.loading || props.error !== null || props.items.length > 0,
);
</script>

<template>
  <section v-if="visible" class="home-chips">
    <header class="home-chips__header">
      <h2 class="home-chips__title">{{ props.title }}</h2>
      <RouterLink
        v-if="props.seeAllTo"
        :to="props.seeAllTo"
        class="home-chips__see-all"
      >
        {{ t("pages.home.seeAll") }}
        <AppIcon name="chevron-right" />
      </RouterLink>
    </header>

    <div
      v-if="props.loading && props.items.length === 0"
      class="home-chips__row"
      aria-hidden="true"
    >
      <SkeletonLoader v-for="n in 6" :key="n" variant="list-row" />
    </div>

    <div v-else-if="props.error" class="home-chips__error" role="alert">
      <span>{{ props.error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="emit('retry')">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div v-else class="home-chips__row">
      <RouterLink
        v-for="chip in props.items"
        :key="chip.name"
        :to="`${props.basePath}/${encodeURIComponent(chip.name)}`"
        class="home-chips__chip"
      >
        {{ chip.name }}
        <span v-if="chip.count !== undefined" class="home-chips__count">
          {{ chip.count }}
        </span>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.home-chips {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.home-chips__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.home-chips__title {
  margin: 0;
  font-size: 1.125rem;
}

.home-chips__see-all {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: 0.875rem;
  text-decoration: none;
  white-space: nowrap;
}

.home-chips__see-all:hover {
  color: var(--color-text);
}

.home-chips__row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.home-chips__row .skeleton {
  width: 7rem;
}

.home-chips__chip {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background-color: var(--color-surface);
  color: var(--color-text);
  text-decoration: none;
  font-weight: 500;
  transition: background-color var(--transition-fast);
}

.home-chips__chip:hover {
  background-color: var(--color-surface-hover);
}

.home-chips__count {
  font-size: 0.75rem;
  color: var(--color-text-muted);
}

.home-chips__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}
</style>
