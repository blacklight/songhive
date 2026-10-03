<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { lookupActivity } from "@/api/activities";
import { remoteLookup } from "@/api/remote";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

// Landing route for local ``/users/{username}/objects/{id}`` ActivityPub
// object permalinks. The backend redirects browser hits on them to the
// object's page (a track-resolved ``Audio`` to ``/tracks/{id}``, an
// activity object to ``/activities/{id}``), but front proxies that serve
// the SPA shell for browser traffic never reach that handler — resolve
// the permalink client-side instead.
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const failed = ref(false);

async function resolve() {
  failed.value = false;
  const objectUrl = `${window.location.origin}${route.path}`;

  // ``remote/lookup`` maps every local permalink shape to its SPA route —
  // ``resolve_local_target`` mirrors the backend's own browser redirect.
  try {
    const result = await remoteLookup(objectUrl);
    // Unresolved permalinks echo the same path back: only follow a route
    // that actually points somewhere else.
    if (result.url && result.url !== route.path) {
      await router.replace(result.url);
      return;
    }
  } catch {
    // Instances gating remote lookups still resolve activity objects
    // through the anonymous-safe activity resolver below.
  }

  try {
    const activity = await lookupActivity(objectUrl);
    await router.replace(`/activities/${activity.id}`);
    return;
  } catch {
    // fall through to the error state
  }
  failed.value = true;
}

watch(
  () => route.fullPath,
  () => void resolve(),
  { immediate: true },
);
</script>

<template>
  <div class="object-permalink">
    <SkeletonLoader v-if="!failed" variant="card" />
    <p v-else class="object-permalink__error" role="alert">
      {{ t("activities.objectUnavailable") }}
    </p>
  </div>
</template>

<style scoped>
.object-permalink {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.object-permalink__error {
  margin: 0;
  padding: var(--space-6);
  color: var(--color-text-muted);
  text-align: center;
}
</style>
