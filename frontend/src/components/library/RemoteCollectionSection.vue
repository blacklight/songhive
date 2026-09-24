<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  listRemoteObjects,
  type RemoteObject,
  type RemoteResourceKind,
} from "@/api/remote";
import { remoteObjectToQueueTrack } from "@/utils/remoteObject";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import AppIcon from "@/components/ui/AppIcon.vue";

/**
 * Collected remote (federated) resources of one kind — rendered inside the
 * per-type browse views while the "my collection" filter is active, since
 * remote objects never join the local entity tables the grid queries.
 */
const props = defineProps<{
  kind: RemoteResourceKind;
  /** Whether the "my collection" filter is on — the section only renders then. */
  active: boolean;
}>();

const { t } = useI18n();
const player = usePlayerStore();
const toastStore = useToastStore();

const items = ref<RemoteObject[]>([]);
const loaded = ref(false);

async function load() {
  if (!props.active) {
    items.value = [];
    loaded.value = false;
    return;
  }
  loaded.value = false;
  try {
    const res = await listRemoteObjects({
      resource_type: props.kind,
      collection: true,
      limit: 50,
    });
    items.value = res.items;
  } catch {
    items.value = [];
  } finally {
    loaded.value = true;
  }
}

function play(item: RemoteObject) {
  const track = remoteObjectToQueueTrack(item);
  if (!track) return;
  try {
    player.playTrack(track);
  } catch (err) {
    toastStore.push({
      type: "error",
      message:
        getApiErrorMessage(err) ||
        t("pages.home.playError", { message: String(err) }),
    });
  }
}

watch(() => [props.kind, props.active], load, { immediate: true });
</script>

<template>
  <section
    v-if="loaded && items.length"
    class="remote-collection"
    data-testid="remote-collection"
  >
    <h2 class="remote-collection__title">
      {{ t("remote.collectedTitle", { kind: t(`remote.kinds.${kind}`) }) }}
    </h2>
    <ul class="remote-collection__list">
      <li v-for="item in items" :key="item.id" class="remote-collection__item">
        <img
          v-if="item.image_url"
          :src="item.image_url"
          :alt="item.name || ''"
          class="remote-collection__thumb"
        />
        <span
          v-else
          class="remote-collection__thumb remote-collection__thumb--empty"
        >
          <AppIcon name="globe" />
        </span>
        <RouterLink :to="item.url" class="remote-collection__link">
          {{ item.name || item.canonical_url }}
        </RouterLink>
        <span class="remote-collection__domain">{{ item.domain }}</span>
        <button
          v-if="item.stream_url || item.audio_url"
          type="button"
          class="remote-collection__play"
          :aria-label="t('common.play')"
          :title="t('common.play')"
          @click="play(item)"
        >
          <AppIcon name="play" />
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.remote-collection {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 60rem;
}

.remote-collection__title {
  margin: 0;
  font-size: 1.125rem;
}

.remote-collection__list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.remote-collection__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.remote-collection__thumb {
  width: 2rem;
  height: 2rem;
  object-fit: cover;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.remote-collection__thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.remote-collection__link {
  color: var(--color-text-link);
  text-decoration: none;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remote-collection__link:hover {
  text-decoration: underline;
}

.remote-collection__domain {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  flex-shrink: 0;
}

.remote-collection__play {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: var(--space-1);
  border-radius: var(--radius-sm);
}

.remote-collection__play:hover {
  color: var(--color-text);
  background-color: var(--color-surface-raised);
}
</style>
