<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { listPublicUsers, type PublicUserResponse } from "@/api/users";
import { getApiErrorMessage } from "@/api/client";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import UserLink from "@/components/user/UserLink.vue";

const { t } = useI18n();

const query = ref("");
const sortBy = ref("username");
const sortDir = ref<"asc" | "desc">("asc");
const offset = ref(0);
const limit = 20;

const users = ref<PublicUserResponse[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);

const hasMore = computed(() => offset.value + users.value.length < total.value);

async function load(replace = true) {
  loading.value = true;
  error.value = null;
  try {
    const result = await listPublicUsers({
      q: query.value || undefined,
      sort_by: sortBy.value,
      sort_dir: sortDir.value,
      limit,
      offset: offset.value,
    });
    if (replace) {
      users.value = result.users;
    } else {
      users.value.push(...result.users);
    }
    total.value = result.total;
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("common.error");
  } finally {
    loading.value = false;
  }
}

function search() {
  offset.value = 0;
  void load();
}

function toggleSort() {
  sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
  search();
}

function loadMore() {
  offset.value += limit;
  void load(false);
}

onMounted(() => void load());
watch([sortBy], search);
</script>

<template>
  <main class="users-directory">
    <h1 class="users-directory__title">{{ t("users.title") }}</h1>

    <form class="users-directory__filters" @submit.prevent="search">
      <AppInput
        v-model="query"
        :label="t('users.search')"
        icon="magnifying-glass"
        @input="offset = 0"
      />
      <AppSelect
        v-model="sortBy"
        :options="[
          { value: 'username', label: t('users.sort.username') },
          { value: 'created_at', label: t('users.sort.createdAt') },
        ]"
        :label="t('users.sort.label')"
      />
      <AppButton type="submit" icon="arrow-up-a-z" @click="toggleSort">
        {{ sortDir === "asc" ? t("common.asc") : t("common.desc") }}
      </AppButton>
    </form>

    <div v-if="loading && !users.length" class="users-directory__loading">
      <SkeletonLoader v-for="n in 5" :key="n" variant="card" />
    </div>
    <div v-else-if="error" class="users-directory__error" role="alert">
      {{ error }}
    </div>
    <p v-else-if="!users.length" class="users-directory__empty">
      {{ t("users.empty") }}
    </p>
    <ul v-else class="users-directory__list">
      <li
        v-for="user in users"
        :key="user.username"
        class="users-directory__item"
      >
        <UserLink
          :username="user.username"
          :display-name="user.display_name"
          :avatar-url="user.avatar_url"
          size="md"
        />
        <p v-if="user.bio" class="users-directory__bio">{{ user.bio }}</p>
        <span class="users-directory__meta">
          {{ user.role }} · {{ t("users.joined") }}
          {{ new Date(user.created_at).toLocaleDateString() }}
        </span>
      </li>
    </ul>

    <div v-if="hasMore && !loading" class="users-directory__more">
      <AppButton variant="secondary" @click="loadMore">{{
        t("common.loadMore")
      }}</AppButton>
    </div>
    <SkeletonLoader v-if="loading && users.length" variant="card" />
  </main>
</template>

<style scoped>
.users-directory {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.users-directory__title {
  margin: 0;
  font-size: 1.75rem;
}

.users-directory__filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: flex-end;
}

.users-directory__filters > *:first-child {
  flex: 1;
  min-width: 12rem;
}

.users-directory__list {
  list-style: none;
  margin: 0;
  padding: 0;
  /*
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  */
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr));
  gap: var(--space-4);
}

.users-directory__item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.users-directory__bio {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.users-directory__meta {
  color: var(--color-text-muted);
  font-size: 0.75rem;
  text-transform: capitalize;
}

.users-directory__empty,
.users-directory__error {
  color: var(--color-text-muted);
}

.users-directory__error {
  color: var(--color-danger);
}

.users-directory__more {
  display: flex;
  justify-content: center;
}
</style>
