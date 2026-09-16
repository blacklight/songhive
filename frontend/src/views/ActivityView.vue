<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  getActivity,
  listActivityQuotes,
  type ActivityResponse,
  type RemoteQuote,
} from "@/api/activities";
import ActivityCard from "@/components/activities/ActivityCard.vue";
import ActivityRemoteReply from "@/components/activities/ActivityRemoteReply.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

// Permalink page for a single activity — the SPA destination of the
// ``{actor}/objects/{id}`` object URLs. Renders the card with its reply
// threads expanded, Mastodon-style, preceded by its ancestor chain so a
// reply opens with its conversation context. Quotes — local ones and
// federated posts carrying a ``quote``/``quoteUrl`` targeting this
// object — are listed under the card.
const { t } = useI18n();
const route = useRoute();

type QuoteEntry =
  | {
      kind: "local";
      key: string;
      publishedAt: string;
      activity: ActivityResponse;
    }
  | { kind: "remote"; key: string; publishedAt: string; quote: RemoteQuote };

const ENTITY_ROUTES: Record<string, string> = {
  track: "tracks",
  album: "albums",
  artist: "artists",
  playlist: "playlists",
  library: "libraries",
};

const MAX_ANCESTORS = 20;

const activity = ref<ActivityResponse | null>(null);
const ancestors = ref<ActivityResponse[]>([]);
const quotes = ref<QuoteEntry[]>([]);
const loading = ref(true);
const failed = ref(false);

async function load(activityId: string) {
  loading.value = true;
  failed.value = false;
  activity.value = null;
  ancestors.value = [];
  quotes.value = [];
  try {
    const main = await getActivity(activityId);
    activity.value = main;
    // Quotes are additive context: a listing failure must not sink the page.
    try {
      const listed = await listActivityQuotes(activityId);
      quotes.value = [
        ...listed.activities.map((a) => ({
          kind: "local" as const,
          key: a.id,
          publishedAt: a.published_at,
          activity: a,
        })),
        ...listed.remote_quotes.map((q) => ({
          kind: "remote" as const,
          key: q.id,
          publishedAt: q.published_at ?? "",
          quote: q,
        })),
      ].sort((a, b) => a.publishedAt.localeCompare(b.publishedAt));
    } catch {
      quotes.value = [];
    }
    const chain: ActivityResponse[] = [];
    const seen = new Set([main.id]);
    let cursor = main.in_reply_to_activity_id ?? null;
    while (cursor && !seen.has(cursor) && chain.length < MAX_ANCESTORS) {
      seen.add(cursor);
      try {
        const parent = await getActivity(cursor);
        chain.unshift(parent);
        cursor = parent.in_reply_to_activity_id ?? null;
      } catch {
        // A deleted or unviewable parent ends the chain.
        break;
      }
    }
    ancestors.value = chain;
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

// ``user`` entities have no activity feed of their own (statuses live on
// the profile), so the back link only exists for content entities.
const backLink = computed(() => {
  const a = activity.value;
  if (!a) return null;
  const plural = ENTITY_ROUTES[a.entity_type];
  if (!plural) return null;
  return {
    to: `/${plural}/${a.entity_id}/activities`,
    label: t("activities.backTo", {
      entity: t(`browse.entities.${a.entity_type}`, a.entity_type),
    }),
  };
});
</script>

<template>
  <div class="activity-view">
    <AppPageTitle icon="comments">{{
      t("activities.detailTitle")
    }}</AppPageTitle>
    <RouterLink v-if="backLink" :to="backLink.to" class="activity-view__back">
      <AppIcon name="arrow-left" spacing="right" />{{ backLink.label }}
    </RouterLink>
    <div class="activity-view__list">
      <SkeletonLoader v-if="loading" variant="card" />
      <p
        v-else-if="failed || !activity"
        class="activity-view__error"
        role="alert"
      >
        {{ t("activities.objectUnavailable") }}
      </p>
      <template v-else>
        <div v-if="ancestors.length" class="activity-view__ancestors">
          <ActivityCard
            v-for="ancestor in ancestors"
            :key="ancestor.id"
            :activity="ancestor"
          />
        </div>
        <ActivityCard :key="activity.id" :activity="activity" expand-replies />
        <section v-if="quotes.length" class="activity-view__quotes">
          <h2 class="activity-view__quotes-title">
            <AppIcon name="quote-left" spacing="right" />{{
              t("activities.quotes")
            }}
          </h2>
          <template v-for="entry in quotes" :key="entry.key">
            <ActivityCard
              v-if="entry.kind === 'local'"
              :activity="entry.activity"
            />
            <ActivityRemoteReply v-else :reply="entry.quote" />
          </template>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.activity-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.activity-view__back {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-secondary);
  text-decoration: none;
}

.activity-view__back:hover {
  text-decoration: underline;
}

.activity-view__list,
.activity-view__quotes {
  width: 100%;
  max-width: 75rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  margin: 0 auto;
  gap: var(--space-3);
}

.activity-view__ancestors {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-left: var(--space-4);
  border-left: 2px solid var(--color-border);
}

.activity-view__quotes-title {
  display: flex;
  align-items: center;
  margin: 0;
  font-size: 1rem;
  color: var(--color-text-secondary);
}

.activity-view__error {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}
</style>
