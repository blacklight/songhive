<script setup lang="ts">
/**
 * Render (possibly remote-supplied) HTML as safe segments.
 *
 * Wraps ``parseActivityContent``: text, line breaks, mentions, hashtags and
 * links survive — the markup itself is never injected, so remote HTML
 * cannot smuggle scripts or unexpected elements into the page.
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";
import {
  parseActivityContent,
  type ContentMark,
  type ContentSegment,
  type MentionEntry,
} from "@/utils/activityContent";

export interface Props {
  html?: string;
  instanceDomain?: string;
  mentions?: MentionEntry[];
}

const props = withDefaults(defineProps<Props>(), {
  html: "",
  instanceDomain: undefined,
  mentions: () => [],
});

const segments = computed(() =>
  parseActivityContent(props.html ?? "", {
    instanceDomain: props.instanceDomain,
    mentions: props.mentions,
  }),
);

// Inline font formatting survived from the source markup — rendered as
// classes rather than real tags so links/mentions keep their own elements.
const MARK_CLASSES: Record<ContentMark, string> = {
  bold: "rich-content__mark--bold",
  italic: "rich-content__mark--italic",
  strikethrough: "rich-content__mark--strikethrough",
  underline: "rich-content__mark--underline",
  code: "rich-content__mark--code",
};

function markClasses(segment: ContentSegment): string[] {
  return (segment.marks ?? []).map((mark) => MARK_CLASSES[mark]);
}
</script>

<template>
  <span class="rich-content">
    <template v-for="(segment, index) in segments" :key="index">
      <template v-if="segment.type === 'text'">
        <span v-if="segment.marks" :class="markClasses(segment)">{{
          segment.value
        }}</span>
        <template v-else>{{ segment.value }}</template>
      </template>
      <RouterLink
        v-else-if="segment.type === 'mention' && segment.username"
        :to="{ name: 'userProfile', params: { username: segment.username } }"
        :class="['rich-content__mention', ...markClasses(segment)]"
        >{{ segment.handle }}</RouterLink
      >
      <a
        v-else-if="segment.type === 'mention'"
        :href="segment.url"
        target="_blank"
        rel="noopener"
        :class="['rich-content__mention', ...markClasses(segment)]"
        >{{ segment.handle }}</a
      >
      <RouterLink
        v-else-if="segment.type === 'tag'"
        :to="{ name: 'tag', params: { name: segment.name } }"
        :class="markClasses(segment)"
        >{{ segment.display }}</RouterLink
      >
      <RouterLink
        v-else-if="segment.to"
        :to="segment.to"
        :class="markClasses(segment)"
        >{{ segment.label }}</RouterLink
      >
      <a
        v-else
        :href="segment.url"
        target="_blank"
        rel="noopener"
        :class="markClasses(segment)"
        >{{ segment.label }}</a
      >
    </template>
  </span>
</template>

<style scoped>
.rich-content {
  white-space: pre-wrap;
  word-break: break-word;
}

.rich-content a {
  color: var(--color-text-link);
}

.rich-content__mention {
  font-weight: 500;
}

.rich-content__mark--bold {
  font-weight: 700;
}

.rich-content__mark--italic {
  font-style: italic;
}

.rich-content__mark--strikethrough {
  text-decoration: line-through;
}

.rich-content__mark--underline {
  text-decoration: underline;
}

.rich-content__mark--strikethrough.rich-content__mark--underline {
  text-decoration: underline line-through;
}

.rich-content__mark--code {
  font-family: ui-monospace, monospace;
  font-size: 0.9em;
  padding: 0 0.2em;
  border-radius: var(--radius-sm);
  background-color: var(--color-surface-secondary);
}
</style>
