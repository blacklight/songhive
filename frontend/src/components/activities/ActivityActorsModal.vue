<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  listActivityBoosts,
  listActivityLikes,
  type ActivityActorResponse,
} from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import AppModal from "@/components/feedback/AppModal.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";

const props = defineProps<{
  open: boolean;
  activityId: string;
  kind: "likes" | "boosts";
}>();
const emit = defineEmits<{ close: [] }>();

const { t } = useI18n();

const actors = ref<ActivityActorResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

async function fetchActors() {
  loading.value = true;
  error.value = null;
  try {
    const response =
      props.kind === "likes"
        ? await listActivityLikes(props.activityId)
        : await listActivityBoosts(props.activityId);
    actors.value = response.actors;
  } catch (err) {
    error.value =
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"));
    actors.value = [];
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.activityId, props.kind],
  ([open]) => {
    if (open) fetchActors();
  },
  { immediate: true },
);
</script>

<template>
  <AppModal
    :open="open"
    :title="t(`activities.actors.${kind}`)"
    @close="emit('close')"
  >
    <div class="activity-actors">
      <AppSpinner v-if="loading" />
      <p v-else-if="error" class="activity-actors__error" role="alert">
        {{ error }}
      </p>
      <p v-else-if="!actors.length" class="activity-actors__empty">
        {{ t("activities.actors.empty") }}
      </p>
      <ul v-else class="activity-actors__list">
        <li v-for="actor in actors" :key="actor.actor">
          <RouterLink
            v-if="actor.username"
            :to="{ name: 'userProfile', params: { username: actor.username } }"
            class="activity-actors__actor"
            @click="emit('close')"
          >
            <AppAvatar
              :src="actor.avatar_url ?? undefined"
              :name="actor.display_name || actor.handle"
              size="sm"
            />
            <span class="activity-actors__names">
              <span class="activity-actors__display-name">{{
                actor.display_name || actor.handle
              }}</span>
              <span class="activity-actors__handle">{{ actor.handle }}</span>
            </span>
          </RouterLink>
          <a
            v-else
            :href="actor.profile_url ?? actor.actor"
            target="_blank"
            rel="noopener"
            class="activity-actors__actor"
          >
            <AppAvatar
              :src="actor.avatar_url ?? undefined"
              :name="actor.display_name || actor.handle"
              size="sm"
            />
            <span class="activity-actors__names">
              <span class="activity-actors__display-name">{{
                actor.display_name || actor.handle
              }}</span>
              <span class="activity-actors__handle">{{ actor.handle }}</span>
            </span>
          </a>
        </li>
      </ul>
    </div>
  </AppModal>
</template>

<style scoped>
.activity-actors {
  min-height: 4rem;
}

.activity-actors__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 60vh;
  overflow-y: auto;
}

.activity-actors__actor {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2);
  border-radius: var(--radius-md);
  text-decoration: none;
}

.activity-actors__actor:hover {
  background-color: var(--color-surface-raised);
}

.activity-actors__names {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.activity-actors__display-name {
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.activity-actors__handle {
  color: var(--color-text-muted);
  font-size: 0.9em;
}

.activity-actors__empty,
.activity-actors__error {
  margin: 0;
  color: var(--color-text-muted);
}
</style>
