<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import {
  listRadios,
  createRadio,
  getRadioTracks,
  type RadioResponse,
  type RadioCreate,
  type Visibility,
} from "@/api/radios";
import { getApiErrorMessage } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { usePlayerStore } from "@/stores/player";
import { useToastStore } from "@/stores/toast";
import { toVisibility } from "@/utils/entity";
import { enrichTracks } from "@/player/enrich";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const auth = useAuthStore();
const player = usePlayerStore();
const toast = useToastStore();

const radios = ref<RadioResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const limit = 20;
const hasMore = ref(false);

const playingId = ref<string | null>(null);

const showCreateForm = computed(() => auth.isAuthenticated);

const newName = ref("");
const newDescription = ref("");
// Default to private; radio stations are opt-in public by design.
const newVisibility = ref<Visibility>("private");
const newConfig = ref("");
const creating = ref(false);
const createError = ref<string | null>(null);

const visibilityOptions = computed(() => [
  { value: "private", label: t("browse.visibility.private") },
  { value: "local", label: t("browse.visibility.local") },
  { value: "public", label: t("browse.visibility.public") },
]);

function getErrorMessage(err: unknown): string {
  return (
    getApiErrorMessage(err) ||
    (err instanceof Error ? err.message : t("errors.unknown"))
  );
}

async function load(reset = false) {
  if (loading.value) return;

  const offset = reset ? 0 : radios.value.length;
  loading.value = true;
  if (reset) {
    error.value = null;
  }

  try {
    const result = await listRadios({ limit, offset });
    if (reset) {
      radios.value = result;
    } else {
      radios.value = [...radios.value, ...result];
    }
    hasMore.value = result.length === limit;
  } catch (err) {
    error.value = t("pages.radio.loadError", {
      message: getErrorMessage(err),
    });
    hasMore.value = false;
  } finally {
    loading.value = false;
  }
}

async function retry() {
  await load(true);
}

async function loadMore() {
  await load();
}

function getVisibilityLabel(value: string): string {
  return t(`browse.visibility.${toVisibility(value)}`);
}

async function onPlay(radio: RadioResponse) {
  playingId.value = radio.id;
  try {
    const tracks = await getRadioTracks(radio.id, { count: 20 });
    if (tracks.length === 0) {
      toast.push({
        type: "warning",
        message: t("pages.radio.emptyTracks"),
      });
      return;
    }
    // Radio tracks come back without denormalized artist/album/artwork.
    // The player will therefore show empty artist/artwork until the backend
    // provides that context or we enrich from artist_id/album_id on demand.
    player.playAll(enrichTracks(tracks));
  } catch (err) {
    toast.push({
      type: "error",
      message: t("pages.radio.playError", {
        message: getErrorMessage(err),
      }),
    });
  } finally {
    playingId.value = null;
  }
}

function resetCreateForm() {
  newName.value = "";
  newDescription.value = "";
  newVisibility.value = "private";
  newConfig.value = "";
  createError.value = null;
}

async function onCreate() {
  const name = newName.value.trim();
  if (!name) return;

  creating.value = true;
  createError.value = null;

  const body: RadioCreate = {
    name,
    description: newDescription.value.trim() || null,
    config: newConfig.value.trim() || null,
  };

  try {
    await createRadio(body, newVisibility.value);
    toast.push({ type: "success", message: t("pages.radio.createSuccess") });
    resetCreateForm();
    await load(true);
  } catch (err) {
    createError.value = t("pages.radio.createError", {
      message: getErrorMessage(err),
    });
  } finally {
    creating.value = false;
  }
}

onMounted(() => load(true));
</script>

<template>
  <div class="radio-view">
    <h1 class="radio-view__title">{{ t("pages.radio.title") }}</h1>

    <div v-if="error" class="radio-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" @click="retry">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <div
      v-else-if="loading && radios.length === 0"
      class="radio-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="radios.length === 0" class="radio-view__empty">
      {{ t("pages.radio.empty") }}
    </div>

    <ul v-else class="radio-view__list" role="list">
      <li v-for="radio in radios" :key="radio.id" class="radio-view__station">
        <div class="radio-view__station-info">
          <h2 class="radio-view__station-name">{{ radio.name }}</h2>
          <p v-if="radio.description" class="radio-view__station-description">
            {{ radio.description }}
          </p>
          <span class="radio-view__station-visibility">
            {{ getVisibilityLabel(radio.visibility) }}
          </span>
        </div>
        <AppButton
          size="sm"
          :loading="playingId === radio.id"
          @click="onPlay(radio)"
        >
          {{ t("common.play") }}
        </AppButton>
      </li>
    </ul>

    <div v-if="!error && hasMore" class="radio-view__footer">
      <AppButton variant="secondary" :loading="loading" @click="loadMore">
        {{ t("browse.list.loadMore") }}
      </AppButton>
    </div>

    <section
      v-if="showCreateForm"
      class="radio-view__create"
      aria-labelledby="radio-create-heading"
    >
      <h2 id="radio-create-heading" class="radio-view__section-title">
        {{ t("pages.radio.createTitle") }}
      </h2>

      <form class="radio-view__create-form" @submit.prevent="onCreate">
        <AppInput
          v-model="newName"
          :label="t('pages.radio.name')"
          :required="true"
        />
        <AppInput
          v-model="newDescription"
          as="textarea"
          :label="t('pages.radio.description')"
        />
        <AppSelect
          v-model="newVisibility"
          :label="t('pages.radio.visibility')"
          :options="visibilityOptions"
        />
        <AppInput
          v-model="newConfig"
          as="textarea"
          :label="t('pages.radio.config')"
          :hint="t('pages.radio.configHint')"
        />

        <div class="radio-view__create-actions">
          <AppButton type="submit" :loading="creating">
            {{ t("pages.radio.create") }}
          </AppButton>
        </div>
      </form>

      <p v-if="createError" class="radio-view__create-error" role="alert">
        {{ createError }}
      </p>
    </section>

    <p v-else class="radio-view__login-hint">
      <RouterLink :to="{ name: 'login' }">
        {{ t("pages.radio.loginHint") }}
      </RouterLink>
    </p>
  </div>
</template>

<style scoped>
.radio-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.radio-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.radio-view__error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.radio-view__skeleton {
  min-height: 16rem;
}

.radio-view__empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.radio-view__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.radio-view__station {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.radio-view__station-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.radio-view__station-name {
  margin: 0;
  font-size: 1.125rem;
}

.radio-view__station-description {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.9375rem;
}

.radio-view__station-visibility {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.radio-view__footer {
  display: flex;
  justify-content: center;
}

.radio-view__create {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.radio-view__section-title {
  margin: 0;
  font-size: 1.25rem;
}

.radio-view__create-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.radio-view__create-actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
}

.radio-view__create-error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.9375rem;
}

.radio-view__login-hint a {
  color: var(--color-accent);
  text-decoration: none;
}

.radio-view__login-hint a:hover {
  text-decoration: underline;
}
</style>
