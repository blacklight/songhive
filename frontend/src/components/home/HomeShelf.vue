<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

/**
 * A titled, horizontally scrolling card shelf.
 *
 * The default slot receives the item cards; the section removes itself
 * entirely once it has loaded with no items — an empty shelf is worse than
 * no shelf. Item width can be tuned by overriding the
 * ``--home-shelf-item-width`` custom property on the shelf element.
 */
export interface Props {
  title: string;
  /** Route path of the "see all" link; omitted renders no link. */
  seeAllTo?: string;
  loading?: boolean;
  error?: string | null;
  /** Number of items rendered by the slot — 0 hides the shelf once loaded. */
  count?: number;
  skeletonCards?: number;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  error: null,
  count: 0,
  skeletonCards: 5,
});

const emit = defineEmits<{ retry: [] }>();
const { t } = useI18n();

const visible = computed(
  () => props.loading || props.error !== null || props.count > 0,
);
</script>

<template>
  <section v-if="visible" class="home-shelf">
    <header class="home-shelf__header">
      <h2 class="home-shelf__title">{{ props.title }}</h2>
      <RouterLink
        v-if="props.seeAllTo"
        :to="props.seeAllTo"
        class="home-shelf__see-all"
      >
        {{ t("pages.home.seeAll") }}
        <AppIcon name="chevron-right" />
      </RouterLink>
    </header>

    <div
      v-if="props.loading && props.count === 0"
      class="home-shelf__scroller"
      aria-hidden="true"
    >
      <SkeletonLoader
        v-for="n in props.skeletonCards"
        :key="n"
        variant="card"
        class="home-shelf__skeleton"
      />
    </div>

    <div v-else-if="props.error" class="home-shelf__error" role="alert">
      <span>{{ props.error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="emit('retry')">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else
      class="home-shelf__scroller"
      role="region"
      tabindex="0"
      :aria-label="props.title"
    >
      <slot />
    </div>
  </section>
</template>

<style scoped>
.home-shelf {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.home-shelf__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.home-shelf__title {
  margin: 0;
  font-size: 1.125rem;
}

.home-shelf__see-all {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: 0.875rem;
  text-decoration: none;
  white-space: nowrap;
}

.home-shelf__see-all:hover {
  color: var(--color-text);
}

.home-shelf__scroller {
  display: flex;
  gap: var(--space-3);
  overflow-x: auto;
  scroll-snap-type: x proximity;
  padding-bottom: var(--space-2);
}

@media (prefers-reduced-motion: no-preference) {
  .home-shelf__scroller {
    scroll-behavior: smooth;
  }
}

/* Slot content carries the parent scope, so size the items via :deep(). */
.home-shelf__scroller > :deep(*) {
  flex: 0 0 var(--home-shelf-item-width, 11rem);
  min-width: 0;
  scroll-snap-align: start;
}

.home-shelf__skeleton {
  min-height: 10rem;
}

.home-shelf__error {
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
