<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { parseActorRef } from "@/utils/actorRef";

const props = defineProps<{
  actorUrl?: string | null;
  displayName?: string | null;
  avatarUrl?: string | null;
}>();

const instanceDomain = useInstanceDomain();

const parsed = computed(() =>
  parseActorRef(props.actorUrl ?? "", instanceDomain.value),
);

const name = computed(
  () =>
    props.displayName?.trim() || parsed.value.username || props.actorUrl || "",
);
</script>

<template>
  <RouterLink
    v-if="parsed.username"
    :to="{ name: 'userProfile', params: { username: parsed.username } }"
    class="actor-card"
  >
    <AppAvatar :src="avatarUrl || ''" :name="name" size="sm" />
    <span class="actor-card__meta">
      <span class="actor-card__name">{{ name }}</span>
      <span class="actor-card__handle">{{ parsed.handle }}</span>
    </span>
    <AppIcon name="chevron-right" class="actor-card__chevron" />
  </RouterLink>
  <a
    v-else-if="parsed.remoteUrl"
    :href="parsed.remoteUrl"
    target="_blank"
    rel="noopener"
    class="actor-card"
  >
    <AppAvatar :src="avatarUrl || ''" :name="name" size="sm" />
    <span class="actor-card__meta">
      <span class="actor-card__name">{{ name }}</span>
      <span class="actor-card__handle">{{ parsed.handle }}</span>
    </span>
    <AppIcon name="arrow-up-right-from-square" class="actor-card__chevron" />
  </a>
  <div v-else class="actor-card actor-card--plain">
    <AppAvatar :src="avatarUrl || ''" :name="name" size="sm" />
    <span class="actor-card__meta">
      <span class="actor-card__name">{{ name || "—" }}</span>
      <span v-if="parsed.handle" class="actor-card__handle">{{
        parsed.handle
      }}</span>
    </span>
  </div>
</template>

<style scoped>
.actor-card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  text-decoration: none;
}

.actor-card:hover:not(.actor-card--plain) {
  border-color: var(--color-accent);
}

.actor-card__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.actor-card__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actor-card__handle {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actor-card__chevron {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
</style>
