<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  listModeratedInstances,
  listModeratedUsers,
  moderateInstance,
  moderateUser,
  unmoderateInstance,
  unmoderateUser,
  type AdminInstanceModerationAction,
  type AdminModeratedActor,
  type AdminModeratedInstance,
  type AdminUserModerationAction,
} from "@/api/admin";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { formatDateTime } from "@/i18n";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

// Admin moderation console — limit/suspend actions on actors plus
// defederate/followers-only policies on remote domains, layered over the
// configured instance block lists. Entries created here are recorded
// with an optional reason and can be reverted in place.
const { t } = useI18n();
const toast = useToastStore();

const limited = ref<AdminModeratedActor[]>([]);
const suspended = ref<AdminModeratedActor[]>([]);
const defederated = ref<AdminModeratedInstance[]>([]);
const followersOnly = ref<AdminModeratedInstance[]>([]);
const isLoading = ref(false);
const error = ref<string | null>(null);
const busy = ref<string | null>(null);

const userForm = ref({
  actor_url: "",
  action: "limit" as AdminUserModerationAction,
  reason: "",
});
const instanceForm = ref({
  domain: "",
  action: "defederate" as AdminInstanceModerationAction,
  reason: "",
});
const userFormBusy = ref(false);
const instanceFormBusy = ref(false);

const userActionOptions = [
  { value: "limit", label: t("moderation.limit") },
  { value: "suspend", label: t("moderation.suspend") },
];
const instanceActionOptions = [
  { value: "defederate", label: t("moderation.defederate") },
  { value: "followers_only", label: t("moderation.followersOnly") },
];

function actorName(actor: AdminModeratedActor): string {
  return actor.display_name || actor.handle || actor.actor_url;
}

function actorLink(actor: AdminModeratedActor): string | null {
  if (actor.local_username) return `/@${actor.local_username}`;
  if (actor.handle) return `/@${actor.handle}`;
  return null;
}

function formatDate(value: string | null | undefined): string {
  return (value && formatDateTime(value)) || "";
}

async function load() {
  isLoading.value = true;
  error.value = null;
  try {
    const [limitRows, suspendRows, defedRows, foRows] = await Promise.all([
      listModeratedUsers({ action: "limit", limit: 100 }),
      listModeratedUsers({ action: "suspend", limit: 100 }),
      listModeratedInstances({ action: "defederate", limit: 100 }),
      listModeratedInstances({ action: "followers_only", limit: 100 }),
    ]);
    limited.value = limitRows;
    suspended.value = suspendRows;
    defederated.value = defedRows;
    followersOnly.value = foRows;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isLoading.value = false;
  }
}

async function submitUserForm() {
  const actorUrl = userForm.value.actor_url.trim();
  if (!actorUrl || userFormBusy.value) return;
  userFormBusy.value = true;
  try {
    await moderateUser({
      actor_url: actorUrl,
      action: userForm.value.action,
      reason: userForm.value.reason.trim() || null,
    });
    userForm.value = { actor_url: "", action: "limit", reason: "" };
    toast.push({ type: "success", message: t("moderation.actionApplied") });
    await load();
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    userFormBusy.value = false;
  }
}

async function submitInstanceForm() {
  const domain = instanceForm.value.domain.trim();
  if (!domain || instanceFormBusy.value) return;
  instanceFormBusy.value = true;
  try {
    await moderateInstance({
      domain,
      action: instanceForm.value.action,
      reason: instanceForm.value.reason.trim() || null,
    });
    instanceForm.value = { domain: "", action: "defederate", reason: "" };
    toast.push({ type: "success", message: t("moderation.actionApplied") });
    await load();
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    instanceFormBusy.value = false;
  }
}

async function clearUser(actor: AdminModeratedActor) {
  if (busy.value) return;
  busy.value = actor.actor_url;
  try {
    await unmoderateUser(actor.actor_url);
    limited.value = limited.value.filter(
      (a) => a.actor_url !== actor.actor_url,
    );
    suspended.value = suspended.value.filter(
      (a) => a.actor_url !== actor.actor_url,
    );
  } catch (err) {
    toast.push({
      type: "error",
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    busy.value = null;
  }
}

async function clearInstance(row: AdminModeratedInstance) {
  if (busy.value) return;
  busy.value = row.domain;
  try {
    await unmoderateInstance(row.domain);
    defederated.value = defederated.value.filter(
      (d) => d.domain !== row.domain,
    );
    followersOnly.value = followersOnly.value.filter(
      (d) => d.domain !== row.domain,
    );
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
  <div class="admin-moderation">
    <AppPageTitle icon="shield">{{ t("moderation.adminTitle") }}</AppPageTitle>

    <p
      v-if="error"
      class="admin-moderation__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <div v-if="isLoading" class="admin-moderation__loading">
      <AppSpinner />
    </div>

    <template v-else>
      <section class="admin-moderation__section">
        <h2 class="admin-moderation__heading">
          {{ t("moderation.usersSection") }}
        </h2>

        <form class="admin-moderation__form" @submit.prevent="submitUserForm">
          <AppInput
            v-model="userForm.actor_url"
            :label="t('moderation.actorUrlLabel')"
            :hint="t('moderation.actorUrlHint')"
            required
          />
          <AppSelect
            v-model="userForm.action"
            :label="t('moderation.actionLabel')"
            :options="userActionOptions"
          />
          <AppInput v-model="userForm.reason" :label="t('moderation.reason')" />
          <AppButton type="submit" :loading="userFormBusy">
            {{ t("moderation.apply") }}
          </AppButton>
        </form>

        <h3 class="admin-moderation__subheading">
          {{ t("moderation.limitedUsers") }}
        </h3>
        <p class="admin-moderation__hint">{{ t("moderation.limitHint") }}</p>
        <ul v-if="limited.length" class="admin-moderation__list" role="list">
          <li
            v-for="actor in limited"
            :key="actor.actor_url"
            class="admin-moderation__row"
          >
            <AppAvatar
              :src="actor.avatar_url || ''"
              :name="actorName(actor)"
              size="sm"
            />
            <div class="admin-moderation__actor">
              <RouterLink
                v-if="actorLink(actor)"
                :to="actorLink(actor)!"
                class="admin-moderation__name"
              >
                {{ actorName(actor) }}
              </RouterLink>
              <span v-else class="admin-moderation__name">{{
                actorName(actor)
              }}</span>
              <span v-if="actor.handle" class="admin-moderation__handle">
                @{{ actor.handle }}
              </span>
              <span v-if="actor.reason" class="admin-moderation__reason">
                {{ actor.reason }}
              </span>
            </div>
            <span class="admin-moderation__date">{{
              formatDate(actor.moderated_at)
            }}</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="busy === actor.actor_url"
              @click="clearUser(actor)"
            >
              {{ t("moderation.clear") }}
            </AppButton>
          </li>
        </ul>
        <p v-else class="admin-moderation__empty" role="status">
          {{ t("moderation.noLimited") }}
        </p>

        <h3 class="admin-moderation__subheading">
          {{ t("moderation.suspendedUsers") }}
        </h3>
        <p class="admin-moderation__hint">{{ t("moderation.suspendHint") }}</p>
        <ul v-if="suspended.length" class="admin-moderation__list" role="list">
          <li
            v-for="actor in suspended"
            :key="actor.actor_url"
            class="admin-moderation__row"
          >
            <AppAvatar
              :src="actor.avatar_url || ''"
              :name="actorName(actor)"
              size="sm"
            />
            <div class="admin-moderation__actor">
              <RouterLink
                v-if="actorLink(actor)"
                :to="actorLink(actor)!"
                class="admin-moderation__name"
              >
                {{ actorName(actor) }}
              </RouterLink>
              <span v-else class="admin-moderation__name">{{
                actorName(actor)
              }}</span>
              <span v-if="actor.handle" class="admin-moderation__handle">
                @{{ actor.handle }}
              </span>
              <span v-if="actor.reason" class="admin-moderation__reason">
                {{ actor.reason }}
              </span>
            </div>
            <span class="admin-moderation__date">{{
              formatDate(actor.moderated_at)
            }}</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="busy === actor.actor_url"
              @click="clearUser(actor)"
            >
              {{ t("moderation.clear") }}
            </AppButton>
          </li>
        </ul>
        <p v-else class="admin-moderation__empty" role="status">
          {{ t("moderation.noSuspended") }}
        </p>
      </section>

      <section class="admin-moderation__section">
        <h2 class="admin-moderation__heading">
          {{ t("moderation.instancesSection") }}
        </h2>

        <form
          class="admin-moderation__form"
          @submit.prevent="submitInstanceForm"
        >
          <AppInput
            v-model="instanceForm.domain"
            :label="t('moderation.domainLabel')"
            :hint="t('moderation.domainHint')"
            required
          />
          <AppSelect
            v-model="instanceForm.action"
            :label="t('moderation.actionLabel')"
            :options="instanceActionOptions"
          />
          <AppInput
            v-model="instanceForm.reason"
            :label="t('moderation.reason')"
          />
          <AppButton type="submit" :loading="instanceFormBusy">
            {{ t("moderation.apply") }}
          </AppButton>
        </form>

        <h3 class="admin-moderation__subheading">
          {{ t("moderation.defederatedInstances") }}
        </h3>
        <p class="admin-moderation__hint">
          {{ t("moderation.defederateHint") }}
        </p>
        <ul
          v-if="defederated.length"
          class="admin-moderation__list"
          role="list"
        >
          <li
            v-for="row in defederated"
            :key="row.domain"
            class="admin-moderation__row"
          >
            <div class="admin-moderation__actor">
              <span class="admin-moderation__name">{{ row.domain }}</span>
              <span v-if="row.reason" class="admin-moderation__reason">
                {{ row.reason }}
              </span>
            </div>
            <span class="admin-moderation__date">{{
              formatDate(row.moderated_at)
            }}</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="busy === row.domain"
              @click="clearInstance(row)"
            >
              {{ t("moderation.clear") }}
            </AppButton>
          </li>
        </ul>
        <p v-else class="admin-moderation__empty" role="status">
          {{ t("moderation.noDefederated") }}
        </p>

        <h3 class="admin-moderation__subheading">
          {{ t("moderation.followersOnlyInstances") }}
        </h3>
        <p class="admin-moderation__hint">
          {{ t("moderation.followersOnlyHint") }}
        </p>
        <ul
          v-if="followersOnly.length"
          class="admin-moderation__list"
          role="list"
        >
          <li
            v-for="row in followersOnly"
            :key="row.domain"
            class="admin-moderation__row"
          >
            <div class="admin-moderation__actor">
              <span class="admin-moderation__name">{{ row.domain }}</span>
              <span v-if="row.reason" class="admin-moderation__reason">
                {{ row.reason }}
              </span>
            </div>
            <span class="admin-moderation__date">{{
              formatDate(row.moderated_at)
            }}</span>
            <AppButton
              size="sm"
              variant="secondary"
              :loading="busy === row.domain"
              @click="clearInstance(row)"
            >
              {{ t("moderation.clear") }}
            </AppButton>
          </li>
        </ul>
        <p v-else class="admin-moderation__empty" role="status">
          {{ t("moderation.noFollowersOnly") }}
        </p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.admin-moderation {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.admin-moderation__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.admin-moderation__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.admin-moderation__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-bottom: var(--space-6);
  border-bottom: 1px solid var(--color-border);
}

.admin-moderation__section:last-child {
  border-bottom: none;
}

.admin-moderation__heading {
  margin: 0;
  font-size: 1.25rem;
}

.admin-moderation__subheading {
  margin: var(--space-2) 0 0;
  font-size: 1rem;
}

.admin-moderation__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.admin-moderation__form {
  display: flex;
  justify-content: space-between;
  /* grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); */
  gap: var(--space-3);
  align-items: flex-start;
  padding: var(--space-3);
  background-color: var(--color-surface-secondary);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

@media (max-width: 75rem) {
  .admin-moderation__form {
    flex-direction: column;
  }
}

.admin-moderation__form button.app-btn--primary {
  margin: auto 0;
}

.admin-moderation__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.admin-moderation__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.admin-moderation__actor {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.admin-moderation__name {
  font-weight: 500;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.admin-moderation__handle {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}

.admin-moderation__reason {
  color: var(--color-text-secondary);
  font-size: 0.85rem;
  overflow-wrap: anywhere;
}

.admin-moderation__date {
  color: var(--color-text-muted);
  font-size: 0.85rem;
  white-space: nowrap;
}

.admin-moderation__empty {
  margin: 0;
  padding: var(--space-4);
  color: var(--color-text-muted);
  text-align: center;
}
</style>
