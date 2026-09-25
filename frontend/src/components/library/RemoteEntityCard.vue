<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import type { RemoteObject } from "@/api/remote";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";

/**
 * Grid card for a cached remote (federated) entity rendered alongside
 * local entities in the browse views. Always carries the owning
 * instance's domain so remote provenance is visible at a glance; the card
 * links to the internal ``/remote/{kind}/{id}`` read-only resource page.
 */
const props = withDefaults(
  defineProps<{
    object: RemoteObject;
    /** ``avatar`` renders a centered circular image (artist grid style). */
    variant?: "cover" | "avatar";
  }>(),
  { variant: "cover" },
);

const { t } = useI18n();

const title = computed(() => props.object.name || props.object.canonical_url);
const subtitle = computed(
  () => props.object.artist_name || props.object.actor_handle || null,
);
</script>

<template>
  <RouterLink
    :to="object.url"
    class="remote-entity-card"
    :class="[
      `remote-entity-card--${variant}`,
      { 'remote-entity-card--unavailable': object.unavailable },
    ]"
  >
    <AppAvatar
      :src="object.image_url ?? undefined"
      :name="title"
      size="lg"
      class="remote-entity-card__media"
    />
    <span :title="title" class="remote-entity-card__name">
      <AppIcon name="globe" class="remote-entity-card__badge" />
      {{ title }}
    </span>
    <span
      v-if="subtitle"
      :title="subtitle"
      class="remote-entity-card__subtitle"
    >
      {{ subtitle }}
    </span>
    <span
      class="remote-entity-card__domain"
      :title="t('remote.hostedOn', { domain: object.domain })"
    >
      <AppIcon name="globe" class="remote-entity-card__domain-icon" />
      {{ object.domain }}
    </span>
  </RouterLink>
</template>

<style scoped>
.remote-entity-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.remote-entity-card:hover {
  background-color: var(--color-surface-hover);
}

.remote-entity-card--unavailable {
  opacity: 0.6;
}

.remote-entity-card--cover .remote-entity-card__media,
.remote-entity-card--cover .remote-entity-card__media.app-avatar--lg {
  width: 100%;
  height: auto;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: var(--radius-md);
  font-size: clamp(2rem, 4vw, 3rem);
  margin-bottom: var(--space-1);
}

.remote-entity-card--avatar {
  align-items: center;
  gap: var(--space-2);
}

.remote-entity-card--avatar .remote-entity-card__media,
.remote-entity-card--avatar .remote-entity-card__media.app-avatar--lg {
  width: 6rem;
  height: 6rem;
  flex-shrink: 0;
  margin-top: var(--space-4);
}

.remote-entity-card__name {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remote-entity-card--cover .remote-entity-card__name {
  justify-content: flex-start;
}

.remote-entity-card__badge {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.remote-entity-card__subtitle {
  font-size: 0.875rem;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remote-entity-card__domain {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 0.75rem;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
