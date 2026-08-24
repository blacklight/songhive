<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, RouterLink } from "vue-router";
import { resolveShareUrl } from "@/api/shares";
import { ApiError } from "@/api/client";
import { parseSharePayload, enrichSharePreview } from "@/utils/share";
import { formatTime } from "@/utils/time";
import AppButton from "@/components/ui/AppButton.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import type { SharePreview } from "@/utils/share";

type ShareError = "expired" | "revoked" | "notFound" | "unknown";

const { t } = useI18n();
const route = useRoute();

const token = computed(() => String(route.params.token));
const loading = ref(false);
const error = ref<ShareError | null>(null);
const preview = ref<SharePreview | null>(null);

const loginRoute = computed(() => ({
  path: "/login",
  query: { redirect: route.fullPath },
}));

const typeLabel = computed(() => {
  if (!preview.value) return t("browse.entities.item");
  const key = preview.value.type === "unknown" ? "item" : preview.value.type;
  return t(`browse.entities.${key}`);
});

const visibilityLabel = computed(() => {
  const value = preview.value?.visibility;
  if (!value) return "";
  const key = `browse.visibility.${value}`;
  const localized = t(key);
  return localized === key ? value : localized;
});

function getShareError(err: unknown): ShareError {
  if (err instanceof ApiError) {
    if (err.status === 410) return "expired";
    if (err.status === 403) return "revoked";
    if (err.status === 404) return "notFound";

    const detail = (err.detail || err.message || "").toLowerCase();
    if (detail.includes("expir")) return "expired";
    if (detail.includes("revok")) return "revoked";
    if (detail.includes("not found")) return "notFound";
  }

  if (err instanceof Error) {
    const message = err.message.toLowerCase();
    if (message.includes("expir")) return "expired";
    if (message.includes("revok")) return "revoked";
    if (message.includes("not found")) return "notFound";
  }

  return "unknown";
}

async function load() {
  loading.value = true;
  error.value = null;
  preview.value = null;

  try {
    const result = await resolveShareUrl(token.value);
    const parsed = parseSharePayload(result);

    if (!parsed) {
      error.value = "unknown";
      return;
    }

    if (!parsed.title) {
      parsed.title = t("browse.share.unknownItem");
    }

    preview.value = await enrichSharePreview(parsed);
  } catch (err) {
    error.value = getShareError(err);
  } finally {
    loading.value = false;
  }
}

onMounted(() => load());
watch(
  () => route.params.token,
  () => load(),
);
</script>

<template>
  <div class="share-view">
    <div v-if="loading" class="share-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div
      v-else-if="error"
      class="share-view__error"
      :class="`share-view__error--${error}`"
      role="alert"
    >
      <h1 class="share-view__title">
        {{ t("browse.share.sharedItem", { type: t("browse.entities.item") }) }}
      </h1>

      <p v-if="error === 'expired'">{{ t("browse.share.shareExpired") }}</p>
      <p v-else-if="error === 'revoked'">
        {{ t("browse.share.shareRevoked") }}
      </p>
      <p v-else-if="error === 'notFound'">
        {{ t("browse.share.shareNotFound") }}
      </p>
      <p v-else>{{ t("errors.unknown") }}</p>

      <RouterLink :to="loginRoute">
        <AppButton size="sm" variant="secondary">
          {{ t("browse.share.openInApp") }}
        </AppButton>
      </RouterLink>
    </div>

    <template v-else-if="preview">
      <h1 class="share-view__title">
        {{ t("browse.share.sharedItem", { type: typeLabel }) }}
      </h1>

      <div class="share-view__header">
        <img
          v-if="preview.coverUrl"
          :src="preview.coverUrl"
          :alt="preview.title"
          class="share-view__cover"
        />

        <div class="share-view__info">
          <h2 class="share-view__name">{{ preview.title }}</h2>

          <p v-if="preview.description" class="share-view__description">
            {{ preview.description }}
          </p>

          <div class="share-view__meta">
            <span v-if="preview.artistName" class="share-view__meta-item">
              {{ t("browse.entities.artist") }} {{ preview.artistName }}
            </span>

            <span v-if="preview.albumTitle" class="share-view__meta-item">
              {{ t("browse.entities.album") }} {{ preview.albumTitle }}
            </span>

            <span v-if="preview.releaseYear" class="share-view__meta-item">
              {{ t("browse.detail.year") }} {{ preview.releaseYear }}
            </span>

            <span v-if="preview.duration" class="share-view__meta-item">
              {{ t("browse.detail.duration") }}
              {{ formatTime(preview.duration) }}
            </span>

            <span v-if="visibilityLabel" class="share-view__meta-item">
              {{ t("browse.detail.visibility") }} {{ visibilityLabel }}
            </span>
          </div>
        </div>
      </div>

      <div class="share-view__actions">
        <RouterLink :to="loginRoute">
          <AppButton size="sm" variant="secondary">
            {{ t("browse.share.openInApp") }}
          </AppButton>
        </RouterLink>
      </div>
    </template>
  </div>
</template>

<style scoped>
.share-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  max-width: 48rem;
}

.share-view__skeleton {
  min-height: 16rem;
}

.share-view__error {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.share-view__title {
  margin: 0;
  font-size: 1.75rem;
}

.share-view__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-5);
  flex-wrap: wrap;
}

.share-view__cover {
  width: 12rem;
  height: 12rem;
  border-radius: var(--radius-lg);
  object-fit: cover;
}

.share-view__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  flex: 1;
  min-width: 16rem;
}

.share-view__name {
  margin: 0;
  font-size: 2rem;
}

.share-view__description {
  margin: 0;
  color: var(--color-text-muted);
  max-width: 40rem;
}

.share-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.share-view__meta-item {
  display: inline-flex;
  align-items: center;
}
</style>
