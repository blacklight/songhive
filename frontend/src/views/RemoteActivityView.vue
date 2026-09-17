<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter, RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import { getRemoteObject, type RemoteObject } from "@/api/remote";
import type { ActivityResponse } from "@/api/activities";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import RichContent from "@/components/RichContent.vue";
import { useInstanceDomain } from "@/composables/useInstanceDomain";

// Permalink for a cached remote object — the SPA destination of
// ``/activities/@user@domain/{remote_object_id}`` links. The page asks the
// backend to refresh the canonical URL once (bounded re-dereference) so a
// remotely deleted object shows its tombstone instead of a stale copy.
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const instanceDomain = useInstanceDomain();

const object = ref<RemoteObject | null>(null);
const activity = ref<ActivityResponse | null>(null);
const loading = ref(true);
const failed = ref(false);

const handle = computed(() => String(route.params.handle ?? ""));

// ``content`` is remote-supplied HTML — RichContent renders it as safe
// segments (text, links, mentions, tags), never verbatim markup.
const objectHtml = computed(() => object.value?.content || "");

async function load(objectId: string) {
  loading.value = true;
  failed.value = false;
  object.value = null;
  activity.value = null;
  try {
    const detail = await getRemoteObject(objectId, { refresh: true });
    object.value = detail.object;
    activity.value = detail.activity ?? null;
    // A remote resource (track/album/…) belongs on the resource view.
    if (!activity.value && detail.object.resource_type) {
      void router.replace(
        `/remote/${detail.object.resource_type}/${detail.object.id}`,
      );
    }
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.params.id,
  (id) => {
    if (typeof id === "string" && id) void load(id);
  },
  { immediate: true },
);
</script>

<template>
  <div class="remote-activity">
    <AppPageTitle icon="comments">{{ t("remote.activityTitle") }}</AppPageTitle>
    <RouterLink v-if="handle" :to="`/@${handle}`" class="remote-activity__back">
      <AppIcon name="arrow-left" spacing="right" />@{{ handle }}
    </RouterLink>
    <div class="remote-activity__list">
      <SkeletonLoader v-if="loading" variant="card" />
      <p
        v-else-if="failed || !object"
        class="remote-activity__error"
        role="alert"
      >
        {{ t("activities.objectUnavailable") }}
      </p>
      <template v-else>
        <p v-if="object.unavailable" class="remote-activity__tombstone">
          {{ t("remote.objectUnavailable") }}
        </p>
        <ActivityCard
          v-if="activity"
          :key="activity.id"
          :activity="activity"
          expand-replies
        />
        <div v-else class="remote-activity__object">
          <p class="remote-activity__remote">
            <AppIcon name="globe" spacing="right" />{{ object.domain }}
          </p>
          <h2 v-if="object.name" class="remote-activity__name">
            {{ object.name }}
          </h2>
          <p v-if="object.summary" class="remote-activity__summary">
            <RichContent
              :html="object.summary"
              :instance-domain="instanceDomain"
            />
          </p>
          <p v-if="objectHtml" class="remote-activity__content">
            <RichContent :html="objectHtml" :instance-domain="instanceDomain" />
          </p>
          <a
            :href="object.canonical_url"
            target="_blank"
            rel="noopener"
            class="remote-activity__origin"
          >
            <AppIcon name="arrow-up-right-from-square" spacing="right" />
            {{ t("remote.viewOriginal", { domain: object.domain }) }}
          </a>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.remote-activity {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.remote-activity__back {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-secondary);
  text-decoration: none;
}

.remote-activity__back:hover {
  text-decoration: underline;
}

.remote-activity__list {
  width: 100%;
  max-width: 75rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  margin: 0 auto;
  gap: var(--space-3);
}

.remote-activity__list > * {
  width: 100%;
}

.remote-activity__error {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}

.remote-activity__tombstone {
  margin: 0;
  color: var(--color-warning, var(--color-text-muted));
}

.remote-activity__object {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.remote-activity__remote {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.remote-activity__name {
  margin: 0;
}

.remote-activity__content {
  word-break: break-word;
}

.remote-activity__origin {
  color: var(--color-text-link);
  text-decoration: none;
}

.remote-activity__origin:hover {
  text-decoration: underline;
}
</style>
