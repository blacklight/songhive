<script setup lang="ts">
import {
  computed,
  nextTick,
  ref,
  useTemplateRef,
  watch,
  type ComponentPublicInstance,
} from "vue";
import { useI18n } from "vue-i18n";
import { usePlayerStore } from "@/stores/player";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import {
  createPlaylist,
  addTracksToPlaylist,
  type AddTracksToPlaylistRequest,
  type PlaylistCreate,
  type Visibility,
} from "@/api/playlists";
import { formatTime } from "@/utils/time";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import { useFocusTrap } from "@/composables/useFocusTrap";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();

type FocusableTarget = HTMLElement | ComponentPublicInstance | null | undefined;

export interface Props {
  open: boolean;
  returnFocusTo?: FocusableTarget;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (e: "close"): void;
}>();

const store = usePlayerStore();
const authStore = useAuthStore();
const toastStore = useToastStore();
const panelRef = useTemplateRef<HTMLElement>("panel");

function isOpen() {
  return props.open;
}

function getPanelRef() {
  return panelRef.value;
}

useFocusTrap(isOpen, getPanelRef);

function scrollToCurrent() {
  const current = panelRef.value?.querySelector<HTMLElement>(
    ".queue-panel__item--current",
  );
  if (!current || typeof current.scrollIntoView !== "function") return;
  current.scrollIntoView({ behavior: "auto", block: "nearest" });
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      nextTick(() => {
        const first = panelRef.value?.querySelector<HTMLElement>(
          'button, [href], input, [tabindex]:not([tabindex="-1"])',
        );
        first?.focus();
        scrollToCurrent();
      });
    } else if (props.returnFocusTo) {
      nextTick(() => {
        const target = props.returnFocusTo;
        if (!target) return;
        if (target instanceof HTMLElement) {
          target.focus();
        } else if ("$el" in target && target.$el instanceof HTMLElement) {
          target.$el.focus();
        }
      });
    }
  },
);

function onKeyDown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
  }
}

function playAtIndex(index: number) {
  store.playAt(index);
}

function removeAt(event: MouseEvent, index: number) {
  event.stopPropagation();
  store.removeAt(index);
}

function clearQueue() {
  store.clear();
  emit("close");
}

type QueueSaveItem = { kind: "track" | "episode" | "remote"; id: string };

/**
 * Flatten the queue into playlist-addable items. Podcast episodes and cached
 * remote objects are playlist members in their own right; attachment-only
 * remote audio (``remote`` without ``remote_object_id``) has no row to
 * reference and is skipped.
 */
function queueSaveItems(): QueueSaveItem[] {
  const items: QueueSaveItem[] = [];
  for (const track of store.queue) {
    if (track.podcast_episode_id) {
      items.push({ kind: "episode", id: track.podcast_episode_id });
    } else if (track.remote && track.remote_object_id) {
      items.push({ kind: "remote", id: track.remote_object_id });
    } else if (!track.remote) {
      items.push({ kind: "track", id: track.id });
    }
  }
  return items;
}

/**
 * Turn saveable items into a sequence of add requests that replays the
 * queue order exactly. A single request appends tracks first, then
 * episodes, then remote objects, and repeated ids inside one request are
 * deduplicated — so the queue is split into contiguous same-kind runs, and
 * a run is split again when an id repeats within it.
 */
function buildSaveBatches(
  items: QueueSaveItem[],
): AddTracksToPlaylistRequest[] {
  const batches: AddTracksToPlaylistRequest[] = [];
  let kind: QueueSaveItem["kind"] | null = null;
  let ids: string[] = [];
  let seen = new Set<string>();

  const flush = () => {
    if (!kind || ids.length === 0) return;
    const body: AddTracksToPlaylistRequest = { allow_duplicates: true };
    if (kind === "track") body.track_ids = ids;
    else if (kind === "episode") body.episode_ids = ids;
    else body.remote_object_ids = ids;
    batches.push(body);
    ids = [];
    seen = new Set();
  };

  for (const item of items) {
    if (item.kind !== kind || seen.has(item.id)) {
      flush();
      kind = item.kind;
    }
    ids.push(item.id);
    seen.add(item.id);
  }
  flush();
  return batches;
}

const hasSaveableItems = computed(() => queueSaveItems().length > 0);

const isSaveOpen = ref(false);
const saveName = ref("");
const saveVisibility = ref<Visibility>("private");
const saveError = ref<string | null>(null);
const isSaving = ref(false);
const isMaximized = ref(false);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

function openSave() {
  saveName.value = "";
  saveVisibility.value = "private";
  saveError.value = null;
  isSaveOpen.value = true;
}

function closeSave() {
  if (!isSaving.value) isSaveOpen.value = false;
}

async function onSaveQueue() {
  saveError.value = null;
  const name = saveName.value.trim();
  if (!name) return;
  const batches = buildSaveBatches(queueSaveItems());
  if (batches.length === 0) return;

  isSaving.value = true;
  try {
    const body: PlaylistCreate = { name, description: null };
    const playlist = await createPlaylist(body, {
      visibility: saveVisibility.value,
    });
    let added = 0;
    for (const batch of batches) {
      const response = await addTracksToPlaylist(playlist.id, batch);
      added += response.added;
    }
    toastStore.push({
      type: "success",
      message: t("player.queueSavedAsPlaylist", { name, count: added }),
    });
    isSaveOpen.value = false;
  } catch (err) {
    saveError.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
  } finally {
    isSaving.value = false;
  }
}
</script>

<template>
  <div
    v-if="open"
    ref="panel"
    class="queue-panel"
    :class="{ 'queue-panel--maximized': isMaximized }"
    role="dialog"
    :aria-label="t('player.queue')"
    aria-modal="true"
    @keydown="onKeyDown"
  >
    <div class="queue-panel__header">
      <AppPageTitle
        :level="2"
        class="queue-panel__title"
        icon="list"
        icon-variant="solid"
      >
        {{ t("player.queue") }}
      </AppPageTitle>
      <AppButton
        variant="ghost"
        size="sm"
        class="queue-panel__maximize"
        :aria-label="t(`common.${isMaximized ? 'shrink' : 'expand'}`)"
        :title="t(`common.${isMaximized ? 'shrink' : 'expand'}`)"
        :icon="isMaximized ? 'compress' : 'expand'"
        @click="isMaximized = !isMaximized"
      />
      <AppButton
        variant="ghost"
        size="sm"
        class="queue-panel__clear"
        :aria-label="t('player.clearQueue')"
        :title="t('player.clearQueue')"
        icon="trash"
        @click="clearQueue"
      />
      <AppButton
        v-if="authStore.isAuthenticated"
        variant="ghost"
        size="sm"
        class="queue-panel__save"
        :aria-label="t('player.saveQueueAsPlaylist')"
        :title="t('player.saveQueueAsPlaylist')"
        icon="floppy-disk"
        :disabled="!hasSaveableItems"
        @click="openSave"
      />
      <AppButton
        variant="ghost"
        size="sm"
        class="queue-panel__close"
        :aria-label="t('player.closeQueue')"
        :title="t('player.closeQueue')"
        icon="xmark"
        @click="emit('close')"
      />
    </div>

    <ol
      class="queue-panel__list"
      role="listbox"
      :aria-label="t('player.queueTracks')"
    >
      <!-- Drag-to-reorder is deferred to a later phase. -->
      <li
        v-for="(track, i) in store.queue"
        :key="`${track.id}:${i}`"
        class="queue-panel__item"
        :class="{ 'queue-panel__item--current': i === store.index }"
        role="option"
        :aria-selected="i === store.index"
        tabindex="0"
        @click="playAtIndex(i)"
        @keydown.enter.prevent="playAtIndex(i)"
      >
        <span class="queue-panel__index" aria-hidden="true">{{ i + 1 }}</span>
        <img
          v-if="track.artwork_url"
          :src="track.artwork_url"
          alt=""
          class="queue-panel__artwork"
        />
        <div
          v-else
          class="queue-panel__artwork queue-panel__artwork--placeholder"
        />
        <div class="queue-panel__meta">
          <p class="queue-panel__track-title" :title="track.title">
            {{ track.title }}
          </p>
          <p class="queue-panel__track-artist" :title="track.artist_name">
            {{ track.artist_name }}
          </p>
        </div>
        <time class="queue-panel__duration" aria-hidden="true">
          {{ formatTime(track.duration ?? 0) }}
        </time>
        <AppButton
          variant="ghost"
          size="sm"
          class="queue-panel__remove"
          :aria-label="t('player.removeFromQueue', { title: track.title })"
          :title="t('player.removeFromQueue', { title: track.title })"
          icon="xmark"
          @click="removeAt($event, i)"
          @keydown.enter.stop
        />
      </li>
    </ol>

    <div v-if="store.queue.length === 0" class="queue-panel__empty">
      {{ t("player.emptyQueue") }}
    </div>

    <AppModal
      :open="isSaveOpen"
      :title="t('player.saveQueueAsPlaylist')"
      @close="closeSave"
    >
      <form
        id="queue-save-form"
        class="queue-panel__save-form"
        @submit.prevent="onSaveQueue"
      >
        <AppInput
          v-model="saveName"
          :label="t('browse.edit.name')"
          :required="true"
          :disabled="isSaving"
        />
        <AppSelect
          v-model="saveVisibility"
          :label="t('browse.detail.visibility')"
          :options="visibilityOptions"
          :disabled="isSaving"
        />
        <p v-if="saveError" class="queue-panel__save-error" role="alert">
          {{ saveError }}
        </p>
      </form>

      <template #actions>
        <AppButton
          variant="secondary"
          icon="xmark"
          :disabled="isSaving"
          @click="closeSave"
        >
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton
          form="queue-save-form"
          type="submit"
          :loading="isSaving"
          :disabled="isSaving || !saveName.trim()"
          icon="floppy-disk"
        >
          {{ t("common.save") }}
        </AppButton>
      </template>
    </AppModal>
  </div>
</template>

<style scoped>
.queue-panel {
  position: fixed;
  bottom: var(--player-bar-height, 5rem);
  right: 0;
  width: min(24rem, calc(100vw - 1rem));
  max-height: min(60vh, 30rem);
  display: flex;
  flex-direction: column;
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  box-shadow: var(--shadow-lg);
  z-index: var(--z-player);
  overflow: hidden;
}

.queue-panel--maximized {
  width: 100%;
  height: 100%;
  max-width: 768px;
  max-height: 80vh;
  border-radius: var(--radius-lg);
}

@media (max-width: 767px) {
  .queue-panel--maximized {
    max-width: none;
    max-height: 70vh;
  }
}

.queue-panel__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.queue-panel__title {
  flex: 1;
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
}

.queue-panel__list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
}

.queue-panel__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  transition: background-color var(--transition-fast);
  outline: none;
}

.queue-panel__item:focus-visible {
  background-color: var(--color-surface-raised);
}

.queue-panel__item:hover {
  background-color: var(--color-surface-hover);
}

.queue-panel__item--current {
  background-color: var(--color-surface-raised);
  border-left: 3px solid var(--color-accent);
}

.queue-panel__index {
  width: 1.5rem;
  text-align: center;
  font-size: 0.75rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.queue-panel__artwork {
  width: 2.5rem;
  height: 2.5rem;
  object-fit: cover;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  background-color: var(--color-surface-raised);
}

.queue-panel__artwork--placeholder {
  background-color: var(--color-border);
}

.queue-panel__meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.queue-panel__track-title,
.queue-panel__track-artist {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-panel__track-title {
  font-weight: 500;
  color: var(--color-text);
}

.queue-panel__track-artist {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.queue-panel__duration {
  font-size: 0.75rem;
  color: var(--color-text-muted);
  flex-shrink: 0;
  min-width: 2.5rem;
  text-align: right;
}

.queue-panel__remove {
  flex-shrink: 0;
  color: var(--color-text-muted);
}

.queue-panel .queue-panel__save,
.queue-panel .queue-panel__clear,
.queue-panel .queue-panel__close,
.queue-panel .queue-panel__remove {
  font-size: 1rem;
}

.queue-panel__save-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.queue-panel__save-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.queue-panel__remove:hover {
  color: var(--color-danger);
}

.queue-panel__empty {
  padding: var(--space-4);
  text-align: center;
  color: var(--color-text-muted);
}

@media (max-width: 767px) {
  .queue-panel {
    left: 0;
    width: auto;
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  }

  /* Keep the header row compact on phones: the "Clear" button collapses to
     its icon (the label stays on the title/aria-label). */
  .queue-panel__clear-label {
    display: none;
  }
}
</style>
