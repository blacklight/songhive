<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  favoriteRemoteObject,
  unfavoriteRemoteObject,
  type RemoteObject,
} from "@/api/remote";
import { remoteObjectToQueueTrack } from "@/utils/remoteObject";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import AppIcon from "@/components/ui/AppIcon.vue";
import ContextMenu, { type MenuItem } from "@/components/ui/ContextMenu.vue";
import AddToCollectionDialog, {
  type CollectionMode,
} from "@/components/library/AddToCollectionDialog.vue";

/**
 * A list of cached remote (federated) resources — tracks, albums, artists,
 * playlists, libraries — rendered with an external marker (globe + source
 * domain) and per-item actions: play, favorite, add to library/playlist,
 * and removal when ``removable`` is set.
 */
const props = defineProps<{
  items: RemoteObject[];
  /** Show a remove action; the parent handles the API call via ``remove``. */
  removable?: boolean;
}>();

const emit = defineEmits<{
  remove: [item: RemoteObject];
  "favorite-changed": [item: RemoteObject];
}>();

const { t } = useI18n();
const authStore = useAuthStore();
const player = usePlayerStore();
const toastStore = useToastStore();

const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);
const menuItem = ref<RemoteObject | null>(null);
const addDialogOpen = ref(false);
const addDialogMode = ref<CollectionMode>("library");
const addDialogItem = ref<RemoteObject | null>(null);

function playable(item: RemoteObject): boolean {
  return !!(item.stream_url || item.audio_url);
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

async function toggleFavorite(item: RemoteObject) {
  const next = !item.favorited;
  try {
    if (next) {
      await favoriteRemoteObject(item.id);
      toastStore.push({ type: "success", message: t("common.favoriteAdded") });
    } else {
      await unfavoriteRemoteObject(item.id);
      toastStore.push({
        type: "success",
        message: t("common.favoriteRemoved"),
      });
    }
    item.favorited = next;
    emit("favorite-changed", item);
  } catch (err) {
    const key = item.favorited
      ? "common.favoriteRemoveError"
      : "common.favoriteAddError";
    toastStore.push({
      type: "error",
      message: t(key, {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  }
}

const menuItems = computed<MenuItem[]>(() => {
  const item = menuItem.value;
  if (!item) return [];
  const items: MenuItem[] = [];
  if (playable(item)) {
    items.push({ key: "play", label: t("common.play"), icon: "play" });
    const track = remoteObjectToQueueTrack(item);
    if (track) {
      items.push({
        key: "enqueue",
        label: t("browse.contextMenu.enqueue"),
        icon: "plus",
      });
    }
  }
  if (authStore.isAuthenticated) {
    items.push({
      key: "add-to-library",
      label: t("browse.contextMenu.addToLibrary"),
      icon: "folder-plus",
    });
    items.push({
      key: "add-to-playlist",
      label: t("browse.contextMenu.addToPlaylist"),
      icon: "list",
    });
    if (item.resource_type === "track") {
      items.push({
        key: "favorite",
        label: item.favorited ? t("common.unfavorite") : t("common.favorite"),
        icon: item.favorited ? "heart-crack" : "heart",
      });
    }
  }
  items.push({
    key: "view-original",
    label: t("remote.viewOriginal", { domain: item.domain }),
    icon: "arrow-up-right-from-square",
  });
  if (props.removable) {
    items.push({
      key: "remove",
      label: t("common.remove"),
      icon: "minus",
      danger: true,
    });
  }
  return items;
});

function openMenu(event: MouseEvent, item: RemoteObject) {
  menuItem.value = item;
  menuX.value = event.clientX;
  menuY.value = event.clientY;
  menuOpen.value = true;
}

function closeMenu() {
  menuOpen.value = false;
}

function openAddDialog(mode: CollectionMode, item: RemoteObject) {
  addDialogMode.value = mode;
  addDialogItem.value = item;
  addDialogOpen.value = true;
}

async function onMenuSelect(key: string) {
  const item = menuItem.value;
  if (!item) return;
  closeMenu();
  switch (key) {
    case "play":
      play(item);
      break;
    case "enqueue": {
      const track = remoteObjectToQueueTrack(item);
      if (track) player.enqueue(track);
      break;
    }
    case "add-to-library":
      openAddDialog("library", item);
      break;
    case "add-to-playlist":
      openAddDialog("playlist", item);
      break;
    case "favorite":
      await toggleFavorite(item);
      break;
    case "view-original":
      window.open(item.canonical_url, "_blank", "noopener,noreferrer");
      break;
    case "remove":
      emit("remove", item);
      break;
  }
}
</script>

<template>
  <ul class="remote-object-list" role="list">
    <li v-for="item in items" :key="item.id" class="remote-object-list__item">
      <div class="remote-object-list__row">
        <img
          v-if="item.image_url"
          :src="item.image_url"
          :alt="item.name || ''"
          class="remote-object-list__thumb"
        />
        <span
          v-else
          class="remote-object-list__thumb remote-object-list__thumb--empty"
        >
          <AppIcon name="globe" />
        </span>
        <span class="remote-object-list__main">
          <RouterLink :to="item.url" class="remote-object-list__link">
            {{ item.name || item.canonical_url }}
          </RouterLink>
          <span
            v-if="item.artist_name"
            class="remote-object-list__subtitle"
            :title="item.artist_name"
          >
            {{ item.artist_name }}
          </span>
        </span>
        <span class="remote-object-list__domain" :title="item.domain">
          <AppIcon name="globe" class="remote-object-list__domain-icon" />
          {{ item.domain }}
        </span>
        <button
          v-if="authStore.isAuthenticated && item.resource_type === 'track'"
          type="button"
          class="remote-object-list__action"
          :class="{ 'remote-object-list__action--favorited': item.favorited }"
          :aria-label="
            item.favorited ? t('common.unfavorite') : t('common.favorite')
          "
          :title="
            item.favorited ? t('common.unfavorite') : t('common.favorite')
          "
          @click="toggleFavorite(item)"
        >
          <AppIcon
            name="heart"
            :variant="item.favorited ? 'solid' : 'regular'"
          />
        </button>
        <button
          v-if="playable(item)"
          type="button"
          class="remote-object-list__action"
          :aria-label="t('common.play')"
          :title="t('common.play')"
          @click="play(item)"
        >
          <AppIcon name="play" />
        </button>
        <button
          type="button"
          class="remote-object-list__action"
          :aria-label="t('browse.detail.actions')"
          :title="t('browse.detail.actions')"
          @click="openMenu($event, item)"
        >
          <AppIcon name="ellipsis-vertical" />
        </button>
      </div>
      <RemoteObjectList
        v-if="item.items?.length"
        :items="item.items"
        :removable="removable"
        class="remote-object-list__children"
        @remove="(child: RemoteObject) => emit('remove', child)"
        @favorite-changed="
          (child: RemoteObject) => emit('favorite-changed', child)
        "
      />
    </li>
  </ul>

  <ContextMenu
    :open="menuOpen"
    :items="menuItems"
    :x="menuX"
    :y="menuY"
    @select="onMenuSelect"
    @close="closeMenu"
  />

  <AddToCollectionDialog
    :open="addDialogOpen"
    :mode="addDialogMode"
    item-type="remote"
    :item-id="addDialogItem?.id"
    :item-name="addDialogItem?.name ?? undefined"
    @close="addDialogOpen = false"
  />
</template>

<style scoped>
.remote-object-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.remote-object-list__item {
  display: flex;
  flex-direction: column;
}

.remote-object-list__row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.remote-object-list__children {
  margin-left: var(--space-6);
}

.remote-object-list__thumb {
  width: 2rem;
  height: 2rem;
  object-fit: cover;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.remote-object-list__thumb--empty {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.remote-object-list__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.remote-object-list__link {
  color: var(--color-text-link);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remote-object-list__link:hover {
  text-decoration: underline;
}

.remote-object-list__subtitle {
  color: var(--color-text-muted);
  font-size: 0.8125rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remote-object-list__domain {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: 0.875rem;
  flex-shrink: 0;
  margin-left: auto;
}

.remote-object-list__action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.remote-object-list__action:hover {
  color: var(--color-text);
  background-color: var(--color-surface-raised);
}

.remote-object-list__action--favorited {
  color: var(--color-danger);
}
</style>
