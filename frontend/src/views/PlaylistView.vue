<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { getPlaylist, type PlaylistResponse } from "@/api/playlists";
import { getApiErrorMessage } from "@/api/client";
import { useEntityMeta } from "@/composables/useEntityMeta";
import { useOwnership } from "@/composables/useOwnership";
import { useShareDialog } from "@/composables/useShareDialog";
import AppBanner from "@/components/feedback/AppBanner.vue";
import AppButton from "@/components/ui/AppButton.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import ShareDialog from "@/components/share/ShareDialog.vue";

const { t } = useI18n();
const route = useRoute();
const playlistId = computed(() => String(route.params.id));

const playlist = ref<PlaylistResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const { ownerName, visibilityText } = useEntityMeta(playlist);
const { isOwner } = useOwnership(
  computed(() => playlist.value?.owner_id ?? null),
);
const { shareOpen, shareTarget, openShare, closeShare } = useShareDialog();

async function load() {
  loading.value = true;
  error.value = null;
  playlist.value = null;

  try {
    playlist.value = await getPlaylist(playlistId.value);
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    loading.value = false;
  }
}

onMounted(() => load());
watch(
  () => route.params.id,
  () => load(),
);
</script>

<template>
  <div class="playlist-view">
    <div v-if="loading && !playlist" class="playlist-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="playlist-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="load">{{ t("common.retry") }}</AppButton>
    </div>

    <template v-else-if="playlist">
      <div class="playlist-view__header">
        <h1 class="playlist-view__name">{{ playlist.name }}</h1>

        <p v-if="playlist.description" class="playlist-view__description">
          {{ playlist.description }}
        </p>

        <div class="playlist-view__meta">
          <span class="playlist-view__meta-item">
            {{ t("browse.detail.visibility") }} {{ visibilityText }}
          </span>
          <span v-if="ownerName" class="playlist-view__meta-item">
            {{ t("browse.detail.owner") }} {{ ownerName }}
          </span>
        </div>

        <div class="playlist-view__header-actions">
          <AppButton
            v-if="isOwner"
            size="sm"
            @click="
              playlist &&
              openShare(
                'playlist',
                playlist.id,
                playlist.name,
                playlist.owner_id,
              )
            "
          >
            {{ t("common.share") }}
          </AppButton>
        </div>
      </div>

      <AppBanner type="info" :title="t('browse.playlist.empty')" />

      <ShareDialog
        v-if="shareTarget"
        :open="shareOpen"
        :item-type="shareTarget.itemType"
        :item-id="shareTarget.itemId"
        :title="shareTarget.title"
        :owner-id="shareTarget.ownerId"
        @close="closeShare"
      />
    </template>
  </div>
</template>

<style scoped>
.playlist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.playlist-view__skeleton {
  min-height: 16rem;
}

.playlist-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.playlist-view__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.playlist-view__name {
  margin: 0;
  font-size: 2rem;
}

.playlist-view__description {
  margin: 0;
  color: var(--color-text-muted);
  max-width: 40rem;
}

.playlist-view__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.playlist-view__header-actions {
  margin-top: var(--space-2);
}
</style>
