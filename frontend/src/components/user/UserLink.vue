<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import { useInstanceStore } from "@/stores/instance";
import type { components } from "@/api/types";

type UserSummary = components["schemas"]["UserSummary"];

interface Props {
  owner?: UserSummary | null;
  username?: string | null;
  displayName?: string | null;
  avatarUrl?: string | null;
  remoteUrl?: string | null;
  showAvatar?: boolean;
  size?: "sm" | "md";
}

const props = withDefaults(defineProps<Props>(), {
  showAvatar: true,
  size: "md",
});

const instanceStore = useInstanceStore();

const instanceDomain = computed(() => {
  if (instanceStore.instance?.uri) {
    try {
      return new URL(instanceStore.instance.uri).host;
    } catch {
      // ignore
    }
  }
  return typeof window !== "undefined" ? window.location.host : "";
});

function isRemoteUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  if (!url.startsWith("http://") && !url.startsWith("https://")) return false;
  try {
    return new URL(url).host !== instanceDomain.value;
  } catch {
    return false;
  }
}

const resolved = computed<UserSummary | null>(() => {
  if (props.owner) return props.owner;
  if (props.username) {
    return {
      id: props.username,
      username: props.username,
      display_name: props.displayName ?? null,
      avatar_url: props.avatarUrl ?? null,
      actor_url: null,
    } as UserSummary;
  }
  return null;
});

const remoteUrl = computed(() => {
  if (props.remoteUrl) return props.remoteUrl;
  const actorUrl = resolved.value?.actor_url;
  if (actorUrl && isRemoteUrl(actorUrl)) return actorUrl;
  return null;
});

const label = computed(
  () =>
    resolved.value?.display_name ||
    resolved.value?.username ||
    remoteUrl.value ||
    "",
);
</script>

<template>
  <a
    v-if="remoteUrl"
    :href="remoteUrl"
    target="_blank"
    rel="noopener"
    class="user-link user-link--remote"
    :class="`user-link--${size}`"
  >
    <AppAvatar
      v-if="showAvatar"
      :src="resolved?.avatar_url || ''"
      :name="label"
      size="sm"
    />
    <span class="user-link__name">{{ label }}</span>
  </a>
  <RouterLink
    v-else-if="resolved?.username"
    :to="{ name: 'userProfile', params: { username: resolved.username } }"
    class="user-link"
    :class="`user-link--${size}`"
  >
    <AppAvatar
      v-if="showAvatar"
      :src="resolved.avatar_url || ''"
      :name="label"
      size="sm"
    />
    <span class="user-link__name">{{ label }}</span>
    <span v-if="size !== 'sm'" class="user-link__handle"
      >@{{ resolved.username }}</span
    >
  </RouterLink>
  <span v-else class="user-link user-link--plain" :class="`user-link--${size}`">
    <AppAvatar
      v-if="showAvatar"
      :src="resolved?.avatar_url || ''"
      :name="label"
      size="sm"
    />
    <span class="user-link__name">{{ label || "—" }}</span>
  </span>
</template>

<style scoped>
.user-link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text);
  text-decoration: none;
  font-weight: 500;
}

.user-link--remote,
.user-link--plain {
  cursor: default;
}

.user-link:hover .user-link__name {
  text-decoration: underline;
}

.user-link__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-link__handle {
  color: var(--color-text-muted);
  font-weight: 400;
  font-size: 0.875rem;
}

.user-link--sm .app-avatar {
  width: 1.25rem;
  height: 1.25rem;
  margin-right: calc(0.5 * var(--space-1));
}
</style>
