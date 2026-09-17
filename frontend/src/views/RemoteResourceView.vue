<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  getRemoteResource,
  type RemoteObject,
  type RemoteResourceKind,
} from "@/api/remote";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import RichContent from "@/components/RichContent.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";

// Read-only page for a cached remote resource (track/album/artist/
// playlist/library). Remote resources never become local rows and carry
// no local management actions — editing, sharing, and library controls
// do not apply.
const { t } = useI18n();
const route = useRoute();
const instanceDomain = useInstanceDomain();

const object = ref<RemoteObject | null>(null);
const loading = ref(true);
const failed = ref(false);

const kind = computed(
  () => String(route.params.kind ?? "") as RemoteResourceKind,
);

const kindLabel = computed(() => t(`remote.kinds.${kind.value}`, kind.value));

// Remote ``summary``/``content`` are HTML documents — RichContent renders
// them as safe segments (text, links, mentions, tags), never verbatim.
const summaryHtml = computed(
  () => object.value?.summary || object.value?.content || "",
);

async function load() {
  const id = String(route.params.id ?? "");
  if (!id || !kind.value) return;
  loading.value = true;
  failed.value = false;
  object.value = null;
  try {
    object.value = await getRemoteResource(kind.value, id);
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

watch(() => [route.params.kind, route.params.id], load, { immediate: true });
</script>

<template>
  <div class="remote-resource">
    <AppPageTitle icon="globe">{{
      t("remote.resourceTitle", { kind: kindLabel })
    }}</AppPageTitle>
    <SkeletonLoader v-if="loading" variant="card" />
    <p
      v-else-if="failed || !object"
      class="remote-resource__error"
      role="alert"
    >
      {{ t("remote.objectUnavailable") }}
    </p>
    <article v-else class="remote-resource__card">
      <img
        v-if="object.image_url"
        :src="object.image_url"
        :alt="object.name || kindLabel"
        class="remote-resource__image"
      />
      <div class="remote-resource__info">
        <p class="remote-resource__remote">
          <span class="remote-resource__badge">
            <AppIcon name="globe" spacing="right" />{{ t("remote.badge") }}
          </span>
          <span class="remote-resource__domain">{{ object.domain }}</span>
        </p>
        <h1 class="remote-resource__name">
          {{ object.name || object.canonical_url }}
        </h1>
        <p v-if="object.unavailable" class="remote-resource__unavailable">
          {{ t("remote.objectUnavailable") }}
        </p>
        <p v-if="summaryHtml" class="remote-resource__summary">
          <RichContent :html="summaryHtml" :instance-domain="instanceDomain" />
        </p>
        <audio
          v-if="object.audio_url"
          :src="object.audio_url"
          controls
          preload="none"
          class="remote-resource__audio"
        />
        <p class="remote-resource__meta">
          <RouterLink
            v-if="object.actor_handle"
            :to="`/@${object.actor_handle}`"
            class="remote-resource__actor"
          >
            @{{ object.actor_handle }}
          </RouterLink>
          <a
            :href="object.canonical_url"
            target="_blank"
            rel="noopener"
            class="remote-resource__origin"
          >
            <AppIcon name="arrow-up-right-from-square" spacing="right" />
            {{ t("remote.viewOriginal", { domain: object.domain }) }}
          </a>
        </p>
      </div>
    </article>
  </div>
</template>

<style scoped>
.remote-resource {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.remote-resource__error {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}

.remote-resource__card {
  display: flex;
  gap: var(--space-4);
  align-items: flex-start;
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  max-width: 60rem;
}

.remote-resource__image {
  width: 10rem;
  height: 10rem;
  object-fit: cover;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.remote-resource__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.remote-resource__remote {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin: 0;
}

.remote-resource__badge {
  font-size: 0.875rem;
  background-color: var(--color-surface-raised);
  color: var(--color-text-secondary);
  padding: calc(1.25 * var(--space-1)) var(--space-2);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.remote-resource__domain {
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.remote-resource__name {
  margin: 0;
  font-size: 1.5rem;
}

.remote-resource__unavailable {
  margin: 0;
  color: var(--color-warning, var(--color-text-muted));
}

.remote-resource__summary {
  margin: 0;
  color: var(--color-text-secondary);
  word-break: break-word;
}

.remote-resource__audio {
  width: 100%;
  max-width: 30rem;
}

.remote-resource__meta {
  display: flex;
  gap: var(--space-4);
  margin: 0;
}

.remote-resource__actor,
.remote-resource__origin {
  color: var(--color-text-link);
  text-decoration: none;
}

.remote-resource__actor:hover,
.remote-resource__origin:hover {
  text-decoration: underline;
}
</style>
