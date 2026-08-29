<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { getStats, type AdminStats } from "@/api/admin";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";
import StatCard from "@/components/admin/StatCard.vue";

const { t } = useI18n();
const toastStore = useToastStore();
const stats = ref<AdminStats | null>(null);
const loading = ref(false);

let intervalId: number | null = null;
const AUTO_REFRESH_MS = 60000;

function formatBytes(bytes: number | undefined): string {
  if (bytes === undefined || bytes === null || Number.isNaN(bytes))
    return "0 B";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size.toFixed(2)} ${units[unitIndex]}`;
}

async function loadStats() {
  if (loading.value) return;
  loading.value = true;
  try {
    stats.value = await getStats();
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.dashboard.loadError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    loading.value = false;
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  intervalId = window.setInterval(loadStats, AUTO_REFRESH_MS);
}

function stopAutoRefresh() {
  if (intervalId !== null) {
    window.clearInterval(intervalId);
    intervalId = null;
  }
}

onMounted(() => {
  void loadStats();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <div class="dashboard-view">
    <header class="dashboard-view__header">
      <AppPageTitle icon="gauge">{{
        t("pages.admin.dashboard.title")
      }}</AppPageTitle>
      <AppButton
        icon="rotate"
        :loading="loading"
        :disabled="loading"
        @click="loadStats"
      >
        {{ t("pages.admin.dashboard.refresh") }}
      </AppButton>
    </header>

    <div v-if="loading && !stats" class="dashboard-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <template v-else-if="stats">
      <section class="dashboard-view__section">
        <h2 class="dashboard-view__section-title">
          <AppIcon name="database" />
          {{ t("pages.admin.dashboard.storage") }}
        </h2>
        <div class="dashboard-view__grid">
          <StatCard
            :label="t('pages.admin.dashboard.totalFiles')"
            :value="stats.storage?.total_files ?? 0"
            icon="file"
            :loading="loading"
          />
          <StatCard
            :label="t('pages.admin.dashboard.totalSize')"
            :value="formatBytes(stats.storage?.total_size_bytes)"
            icon="hard-drive"
            :loading="loading"
          />
        </div>
      </section>

      <section class="dashboard-view__section">
        <h2 class="dashboard-view__section-title">
          <AppIcon name="users" />
          {{ t("pages.admin.dashboard.users") }}
        </h2>
        <div class="dashboard-view__grid dashboard-view__grid--3">
          <StatCard
            :label="t('pages.admin.dashboard.totalUsers')"
            :value="stats.users?.total_users ?? 0"
            icon="users"
            :loading="loading"
          />
          <StatCard
            :label="t('pages.admin.dashboard.activeUsers')"
            :value="stats.users?.active_users ?? 0"
            icon="user-check"
            :loading="loading"
          />
          <StatCard
            :label="t('pages.admin.dashboard.recentRegistrations')"
            :value="stats.users?.recent_registrations ?? 0"
            icon="user-plus"
            :loading="loading"
          />
        </div>
      </section>

      <section class="dashboard-view__section">
        <h2 class="dashboard-view__section-title">
          <AppIcon name="music" />
          {{ t("pages.admin.dashboard.content") }}
        </h2>
        <div class="dashboard-view__grid dashboard-view__grid--4">
          <StatCard
            :label="t('nav.tracks')"
            :value="stats.content?.total_tracks ?? 0"
            icon="music"
            :loading="loading"
          />
          <StatCard
            :label="t('nav.albums')"
            :value="stats.content?.total_albums ?? 0"
            icon="compact-disc"
            :loading="loading"
          />
          <StatCard
            :label="t('nav.playlists')"
            :value="stats.content?.total_playlists ?? 0"
            icon="list"
            :loading="loading"
          />
          <StatCard
            :label="t('pages.admin.dashboard.libraries')"
            :value="stats.content?.total_libraries ?? 0"
            icon="folder-open"
            :loading="loading"
          />
        </div>
      </section>

      <section v-if="stats.federation" class="dashboard-view__section">
        <h2 class="dashboard-view__section-title">
          <AppIcon name="globe" />
          {{ t("pages.admin.dashboard.federation") }}
        </h2>
        <div class="dashboard-view__grid dashboard-view__grid--3">
          <StatCard
            :label="t('pages.admin.dashboard.federation')"
            :value="
              stats.federation.enabled
                ? t('pages.admin.dashboard.federationEnabled')
                : t('pages.admin.dashboard.federationDisabled')
            "
            icon="power-off"
            :loading="loading"
          />
          <StatCard
            :label="t('pages.admin.dashboard.instanceDomain')"
            :value="stats.federation.instance_domain || '—'"
            icon="server"
            :loading="loading"
          />
          <StatCard
            :label="t('pages.admin.dashboard.instanceName')"
            :value="stats.federation.instance_name || '—'"
            icon="font"
            :loading="loading"
          />
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.dashboard-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.dashboard-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}

.dashboard-view__skeleton {
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.dashboard-view__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.dashboard-view__section-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--color-text);
}

.dashboard-view__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: var(--space-3);
}

.dashboard-view__grid--3 {
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
}

.dashboard-view__grid--4 {
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
}
</style>
