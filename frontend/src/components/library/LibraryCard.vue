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

const owner = computed(() => {
  const ownerId = props.library.owner_id;
  if (!ownerId) return null;
  if (authStore.user?.id === ownerId) {
    return authStore.user;
  }
  // The backend only returns owner_id for other users; resolving their
  // display names requires a denormalized field or a user lookup.
  return null;
});

const ownerName = computed(() => {
  if (!owner?.value) return "";
  return owner.value.display_name ?? owner.value.username;
});

const ownerAvatarUrl = computed(() => {
  if (!owner?.value) return "";
  return owner.value.avatar_url ?? "";
});

const visibilityText = computed(() => {
  const labels: Record<string, string> = {
    private: t("browse.visibility.private"),
    local: t("browse.visibility.local"),
    public: t("browse.visibility.public"),
  };
  return labels[props.library.visibility] ?? props.library.visibility;
});

const visibilityIcon = computed(() => {
  const icons: Record<string, string> = {
    private: "fas fa-lock",
    local: "fas fa-home",
    public: "fas fa-globe",
  };
  return icons[props.library.visibility] ?? "mdi-help-circle";
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
    <span :title="props.library.name" class="library-card__name">{{
      props.library.name
    }}</span>
    <span
      v-if="props.library.description"
      :title="props.library.description"
      class="library-card__description"
    >
      {{ props.library.description }}
    </span>
    <div class="library-card__meta">
      <span v-if="ownerName" :title="ownerName" class="library-card__owner">
        <AppAvatar
          v-if="ownerAvatarUrl"
          :src="ownerAvatarUrl"
          :name="ownerName"
          width="16px"
        />

        {{ ownerName }}
      </span>
      <span :title="visibilityText" class="library-card__visibility">
        <i :class="visibilityIcon" />
      </span>
    </div>
  </RouterLink>
</template>

<style scoped>
.library-card {
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

.library-card:hover {
  background-color: var(--color-surface-hover);
}

.library-card__name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.library-card__description {
  font-size: 0.875rem;
  color: var(--color-text-muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.library-card__meta {
  display: flex;
  font-size: 0.75rem;
  opacity: 0.75;
  flex-wrap: wrap;
  gap: calc(0.5 * var(--space-1));
  color: var(--color-text-muted);
  margin-top: var(--space-1);
}

.library-card__visibility,
.library-card__owner {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.library-card__owner {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.library-card__visibility {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: flex-end;
}

.library-card__owner .app-avatar--initials {
  margin: 0 var(--space-1) 0 0;
  font-size: 0.55rem;
}

:deep(.library-card__owner img) {
  margin: 0;
}
</style>
