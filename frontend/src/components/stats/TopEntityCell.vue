<script setup lang="ts">
import AppIcon from "@/components/ui/AppIcon.vue";
import type { StatsTopEntry } from "@/api/stats";

const props = withDefaults(
  defineProps<{
    entry: StatsTopEntry;
    /** Fallback icon when the entity has no artwork. */
    icon?: string;
  }>(),
  { icon: "music" },
);
</script>

<template>
  <span class="top-entity">
    <img
      v-if="props.entry.image_url"
      :src="props.entry.image_url"
      class="top-entity__thumb"
      alt=""
    />
    <span v-else class="top-entity__thumb top-entity__thumb--empty">
      <AppIcon :name="props.icon" />
    </span>
    <span class="top-entity__body">
      <RouterLink
        v-if="props.entry.url"
        :to="props.entry.url"
        class="top-entity__name"
      >
        {{ props.entry.name }}
      </RouterLink>
      <span v-else class="top-entity__name">{{ props.entry.name }}</span>
      <RouterLink
        v-if="props.entry.artist_url && props.entry.artist_name"
        :to="props.entry.artist_url"
        class="top-entity__artist"
      >
        {{ props.entry.artist_name }}
      </RouterLink>
      <span v-else-if="props.entry.artist_name" class="top-entity__artist">
        {{ props.entry.artist_name }}
      </span>
    </span>
  </span>
</template>

<style scoped>
.top-entity {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  max-width: 100%;
}

.top-entity__thumb {
  width: 2rem;
  height: 2rem;
  border-radius: var(--radius-sm);
  object-fit: cover;
  flex-shrink: 0;
}

.top-entity__thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  background-color: var(--color-surface-hover);
}

.top-entity__body {
  display: inline-flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.25;
}

.top-entity__name {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.top-entity__artist {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

a.top-entity__name:hover,
a.top-entity__artist:hover {
  text-decoration: underline;
}
</style>
