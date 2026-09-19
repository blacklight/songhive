<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listBlocks,
  listMutes,
  unblockActor,
  unmuteActor,
  type ModeratedActor,
} from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

// Personal moderation lists — the mutes and blocks recorded by the
// current user, each with an undo action. Profile cards record new
// entries; this tab is where they are reviewed and reverted.
const { t } = useI18n();
const toast = useToastStore();

const mutes = ref<ModeratedActor[]>([]);
const blocks = ref<ModeratedActor[]>([]);
const isLoading = ref(false);
const error = ref<string | null>(null);
const busy = ref<string | null>(null);

function actorName(actor: ModeratedActor): string {
  return actor.display_name || actor.handle || actor.actor_url;
}

function actorLink(actor: ModeratedActor): string | null {
  if (actor.local_username) {
    return `/@${actor.local_username}`;
  }
  if (actor.handle) {
    return `/@${actor.handle}`;
  }
  return null;
}

function formatDate(value: string | null | undefined): string {
  return (value && formatDateTime(value)) || "";
}

async function load() {
  isLoading.value = true;
  error.value = null;
  try {
    const [muteResult, blockResult] = await Promise.all([
      listMutes({ limit: 100 }),
      listBlocks({ limit: 100 }),
    ]);
    mutes.value = muteResult.actors;
    blocks.value = blockResult.actors;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isLoading.value = false;
  }
}

async function unmute(actor: ModeratedActor) {
  if (busy.value) return;
  busy.value = actor.actor_url;
  try {
    await unmuteActor(actor.actor_url);
    mutes.value = mutes.value.filter((a) => a.actor_url !== actor.actor_url);
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    busy.value = null;
  }
}

async function unblock(actor: ModeratedActor) {
  if (busy.value) return;
  busy.value = actor.actor_url;
  try {
    await unblockActor(actor.actor_url);
    blocks.value = blocks.value.filter((a) => a.actor_url !== actor.actor_url);
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    busy.value = null;
  }
}

onMounted(load);
</script>

<template>
  <div class="moderation-tab">
    <p
      v-if="error"
      class="moderation-tab__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <div v-if="isLoading" class="moderation-tab__loading">
      <AppSpinner />
    </div>

    <template v-else>
      <section class="moderation-tab__section">
        <h3 class="moderation-tab__heading">
          {{ t("moderation.mutedUsers") }}
        </h3>
        <p class="moderation-tab__hint">{{ t("moderation.mutesHint") }}</p>
        <ul v-if="mutes.length" class="moderation-tab__list" role="list">
          <li
            v-for="actor in mutes"
            :key="actor.actor_url"
            class="moderation-tab__row"
          >
            <AppAvatar
              :src="actor.avatar_url || ''"
              :name="actorName(actor)"
              size="sm"
            />
            <div class="moderation-tab__actor">
              <RouterLink
                v-if="actorLink(actor)"
                :to="actorLink(actor)!"
                class="moderation-tab__name"
              >
                {{ actorName(actor) }}
              </RouterLink>
              <span v-else class="moderation-tab__name">{{
                actorName(actor)
              }}</span>
              <span v-if="actor.handle" class="moderation-tab__handle">
                @{{ actor.handle }}
              </span>
            </div>
            <span class="moderation-tab__date">{{
              formatDate(actor.moderated_at)
            }}</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="busy === actor.actor_url"
              @click="unmute(actor)"
            >
              {{ t("moderation.unmute") }}
            </AppButton>
          </li>
        </ul>
        <p v-else class="moderation-tab__empty" role="status">
          {{ t("moderation.noMutes") }}
        </p>
      </section>

      <section class="moderation-tab__section">
        <h3 class="moderation-tab__heading">
          {{ t("moderation.blockedUsers") }}
        </h3>
        <p class="moderation-tab__hint">{{ t("moderation.blocksHint") }}</p>
        <ul v-if="blocks.length" class="moderation-tab__list" role="list">
          <li
            v-for="actor in blocks"
            :key="actor.actor_url"
            class="moderation-tab__row"
          >
            <AppAvatar
              :src="actor.avatar_url || ''"
              :name="actorName(actor)"
              size="sm"
            />
            <div class="moderation-tab__actor">
              <RouterLink
                v-if="actorLink(actor)"
                :to="actorLink(actor)!"
                class="moderation-tab__name"
              >
                {{ actorName(actor) }}
              </RouterLink>
              <span v-else class="moderation-tab__name">{{
                actorName(actor)
              }}</span>
              <span v-if="actor.handle" class="moderation-tab__handle">
                @{{ actor.handle }}
              </span>
            </div>
            <span class="moderation-tab__date">{{
              formatDate(actor.moderated_at)
            }}</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="busy === actor.actor_url"
              @click="unblock(actor)"
            >
              {{ t("moderation.unblock") }}
            </AppButton>
          </li>
        </ul>
        <p v-else class="moderation-tab__empty" role="status">
          {{ t("moderation.noBlocks") }}
        </p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.moderation-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.moderation-tab__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.moderation-tab__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.moderation-tab__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.moderation-tab__heading {
  margin: 0;
  font-size: 1rem;
}

.moderation-tab__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.moderation-tab__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.moderation-tab__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.moderation-tab__actor {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.moderation-tab__name {
  font-weight: 500;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.moderation-tab__handle {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}

.moderation-tab__date {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  white-space: nowrap;
}

.moderation-tab__empty {
  margin: 0;
  padding: var(--space-4);
  color: var(--color-text-muted);
  text-align: center;
}
</style>
