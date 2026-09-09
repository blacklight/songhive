<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import { parseRichText, type RichTextToken } from "@/utils/richText";

export interface Props {
  text?: string;
  instanceDomain?: string;
}

const props = withDefaults(defineProps<Props>(), {
  text: "",
  instanceDomain: undefined,
});

const tokens = computed<RichTextToken[]>(() =>
  parseRichText(props.text ?? "", props.instanceDomain),
);
</script>

<template>
  <span class="rich-text">
    <template v-for="(token, index) in tokens" :key="index">
      <template v-if="token.type === 'text'">{{ token.value }}</template>

      <a
        v-else-if="token.type === 'url'"
        :href="token.url"
        target="_blank"
        rel="noopener"
        class="rich-text__link"
        >{{ token.label }}</a
      >

      <RouterLink
        v-else-if="token.type === 'mention' && !token.remote"
        :to="{ name: 'userProfile', params: { username: token.username } }"
        class="rich-text__mention"
        >{{ token.handle }}</RouterLink
      >

      <a
        v-else-if="token.type === 'mention' && token.remote"
        :href="token.url"
        target="_blank"
        rel="noopener"
        class="rich-text__mention rich-text__mention--remote"
        >{{ token.handle }}</a
      >

      <RouterLink
        v-else-if="token.type === 'tag'"
        :to="{ name: 'tag', params: { name: token.name } }"
        class="rich-text__tag"
        >{{ token.display }}</RouterLink
      >
    </template>
  </span>
</template>

<style scoped>
.rich-text {
  display: inline;
}

.rich-text__link,
.rich-text__mention,
.rich-text__tag {
  color: var(--color-text-muted);
  text-decoration: none;
}

.rich-text__link:hover,
.rich-text__mention:hover,
.rich-text__tag:hover {
  color: var(--color-text-hover);
  text-decoration: underline;
}
</style>
