<script setup lang="ts">
import { RouterLink } from "vue-router";
import type { PublicUserResponse } from "@/api/users";
import AppAvatar from "@/components/ui/AppAvatar.vue";

/** Compact user tile for the "People on this instance" shelf. */
export interface Props {
  user: PublicUserResponse;
}

const props = defineProps<Props>();
</script>

<template>
  <RouterLink
    :to="`/@${props.user.username}`"
    class="home-user-card"
    :title="`@${props.user.username}`"
  >
    <AppAvatar
      :src="props.user.avatar_url ?? undefined"
      :name="props.user.display_name || props.user.username"
      size="lg"
      class="home-user-card__avatar"
    />
    <span class="home-user-card__name">
      {{ props.user.display_name || props.user.username }}
    </span>
    <span class="home-user-card__handle">@{{ props.user.username }}</span>
  </RouterLink>
</template>

<style scoped>
.home-user-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  text-align: center;
  transition: background-color var(--transition-fast);
}

.home-user-card:hover {
  background-color: var(--color-surface-hover);
}

.home-user-card__avatar {
  width: 4rem;
  height: 4rem;
}

.home-user-card__name {
  font-weight: 600;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-user-card__handle {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
