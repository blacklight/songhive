<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import type { LibraryResponse } from "@/api/libraries";
import AppAvatar from "@/components/ui/AppAvatar.vue";

export interface Props {
  library: LibraryResponse;
}

const props = defineProps<Props>();
const emit = defineEmits<{ click: [library: LibraryResponse] }>();
const { t } = useI18n();
const authStore = useAuthStore();

const ownerName = computed(() => {
  const ownerId = props.library.owner_id;
  if (!ownerId) return "";
  if (authStore.user?.id === ownerId) {
    return authStore.user.display_name ?? authStore.user.username;
  }
  // The backend only returns owner_id for other users; resolving their
  // display names requires a denormalized field or a user lookup.
  return ownerId;
});

const visibilityText = computed(() => {
  const labels: Record<string, string> = {
    private: t("browse.visibility.private"),
    local: t("browse.visibility.local"),
    public: t("browse.visibility.public"),
  };
  return labels[props.library.visibility] ?? props.library.visibility;
});
</script>

<template>
  <RouterLink
    :to="`/libraries/${props.library.id}`"
    class="library-card"
    @click="emit('click', props.library)"
  >
    <AppAvatar
      :name="props.library.name"
      size="lg"
      class="library-card__avatar"
    />
    <span class="library-card__name">{{ props.library.name }}</span>
    <span v-if="props.library.description" class="library-card__description">
      {{ props.library.description }}
    </span>
    <div class="library-card__meta">
      <span class="library-card__visibility">{{ visibilityText }}</span>
      <span v-if="ownerName" class="library-card__owner">
        {{ t("browse.detail.owner") }} {{ ownerName }}
      </span>
    </div>
  </RouterLink>
</template>

<style scoped>
.library-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}

.library-card:hover {
  background-color: var(--color-bg-hover);
}

.library-card__name {
  font-weight: 600;
}

.library-card__description {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.library-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: 0.875rem;
  color: var(--color-text-muted);
}
</style>
