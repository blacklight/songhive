<script setup lang="ts">
import { computed } from "vue";
import type { RemoteReply } from "@/api/activities";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import ActivityAudioPlayer from "./ActivityAudioPlayer.vue";
import { formatDateTime } from "@/i18n";
import { useInstanceDomain } from "@/composables/useInstanceDomain";
import { parseActivityContent } from "@/utils/activityContent";

const props = defineProps<{ reply: RemoteReply }>();

const instanceDomain = useInstanceDomain();

const actorName = computed(() => {
  const actor = props.reply.source_actor;
  try {
    const url = new URL(actor);
    const shortName =
      url.pathname.split("/").filter(Boolean).pop() ?? url.hostname;
    return `@${shortName}@${url.hostname}`;
  } catch {
    return actor;
  }
});

const displayName = computed(
  () => props.reply.source_actor_name?.trim() || actorName.value,
);

const profileUrl = computed(
  () => props.reply.source_actor_url || props.reply.source_actor,
);

const contentSegments = computed(() =>
  parseActivityContent(props.reply.content ?? "", {
    instanceDomain: instanceDomain.value,
    mentions: [],
  }),
);

const imageAttachments = computed(() =>
  (props.reply.attachments ?? []).filter(
    (a) => a.url && (a.mediaType ?? "").startsWith("image/"),
  ),
);
const audioAttachments = computed(() =>
  (props.reply.attachments ?? []).filter(
    (a) =>
      a.url && (a.type === "Audio" || (a.mediaType ?? "").startsWith("audio/")),
  ),
);
const fileAttachments = computed(() =>
  (props.reply.attachments ?? []).filter(
    (a) =>
      a.url &&
      !imageAttachments.value.includes(a) &&
      !audioAttachments.value.includes(a),
  ),
);
</script>

<template>
  <article class="remote-reply">
    <header class="remote-reply__header">
      <AppAvatar
        :src="reply.source_actor_avatar_url ?? undefined"
        :name="displayName"
        size="sm"
      />
      <div class="remote-reply__meta">
        <a
          :href="profileUrl"
          target="_blank"
          rel="noopener"
          class="remote-reply__actor"
        >
          <span class="remote-reply__display-name">{{ displayName }}</span>
          <span class="remote-reply__handle">{{ actorName }}</span>
        </a>
        <a
          v-if="reply.published_at"
          :href="reply.url ?? reply.object_id ?? undefined"
          target="_blank"
          rel="noopener"
          class="remote-reply__time"
          >{{ formatDateTime(reply.published_at) }}</a
        >
      </div>
    </header>

    <p
      v-if="contentSegments.length"
      class="remote-reply__content"
      :lang="reply.language || undefined"
    >
      <template v-for="(segment, index) in contentSegments" :key="index">
        <template v-if="segment.type === 'text'">{{ segment.value }}</template>
        <RouterLink
          v-else-if="segment.type === 'mention' && segment.username"
          :to="{ name: 'userProfile', params: { username: segment.username } }"
          class="remote-reply__mention"
          >{{ segment.handle }}</RouterLink
        >
        <a
          v-else-if="segment.type === 'mention'"
          :href="segment.url"
          target="_blank"
          rel="noopener"
          class="remote-reply__mention"
          >{{ segment.handle }}</a
        >
        <RouterLink
          v-else-if="segment.type === 'tag'"
          :to="{ name: 'tag', params: { name: segment.name } }"
          >{{ segment.display }}</RouterLink
        >
        <RouterLink v-else-if="segment.to" :to="segment.to">{{
          segment.label
        }}</RouterLink>
        <a v-else :href="segment.url" target="_blank" rel="noopener">{{
          segment.label
        }}</a>
      </template>
    </p>

    <div v-if="reply.attachments?.length" class="remote-reply__attachments">
      <a
        v-for="(attachment, index) in imageAttachments"
        :key="`image-${index}`"
        :href="attachment.url"
        target="_blank"
        rel="noopener"
      >
        <img
          :src="attachment.url"
          :alt="attachment.name ?? ''"
          class="remote-reply__image"
          loading="lazy"
        />
      </a>
      <ActivityAudioPlayer
        v-for="(attachment, index) in audioAttachments"
        :key="`audio-${index}`"
        :attachment="attachment"
        :avatar-url="reply.source_actor_avatar_url ?? undefined"
        remote
      />
      <ul v-if="fileAttachments.length" class="remote-reply__file-list">
        <li
          v-for="(attachment, index) in fileAttachments"
          :key="`file-${index}`"
        >
          <a :href="attachment.url" target="_blank" rel="noopener">{{
            attachment.name || attachment.url
          }}</a>
        </li>
      </ul>
    </div>
  </article>
</template>

<style scoped>
.remote-reply {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-secondary);
}

.remote-reply__header {
  display: flex;
  gap: var(--space-3);
}

.remote-reply__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.remote-reply__actor {
  display: flex;
  flex-direction: column;
  text-decoration: none;
}

.remote-reply__display-name {
  font-weight: 600;
  color: var(--color-text);
}

.remote-reply__handle,
.remote-reply__time {
  color: var(--color-text-muted);
  font-size: 0.9em;
  text-decoration: none;
}

.remote-reply__time {
  font-size: 0.8rem;
}

.remote-reply__content {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.remote-reply__content a {
  color: var(--color-text-link);
}

.remote-reply__mention {
  font-weight: 500;
}

.remote-reply__attachments {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.remote-reply__image {
  max-width: 100%;
  max-height: 16rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  object-fit: contain;
}

.remote-reply__file-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.remote-reply__file-list a {
  color: var(--color-text-link);
  word-break: break-all;
}
</style>
