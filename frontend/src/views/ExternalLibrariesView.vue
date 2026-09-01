<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import {
  listUserExternalLibraries,
  adminListExternalLibraries,
  listUserProviders,
  listAdminProviders,
  type ExternalLibraryResponse,
  type ExternalProviderResponse,
} from "@/api/externalLibraries";
import { getApiErrorMessage } from "@/api/client";
import AppButton from "@/components/ui/AppButton.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppTable from "@/components/ui/AppTable.vue";
import AppPagination from "@/components/ui/AppPagination.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();

const isAdmin = computed(() => route.path.startsWith("/admin"));
const basePath = computed(() =>
  isAdmin.value ? "/admin/external-libraries" : "/profile/external-libraries",
);

const libraries = ref<ExternalLibraryResponse[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const total = ref(0);
const page = ref(1);
const perPage = 20;
const providers = ref<ExternalProviderResponse[]>([]);

const columns = computed(() => [
  { key: "name", label: t("pages.externalLibraries.name") },
  { key: "provider_type", label: t("pages.externalLibraries.provider") },
  {
    key: "enabled",
    label: t("pages.externalLibraries.enabled"),
    align: "center" as const,
  },
  {
    key: "actions",
    label: t("browse.detail.actions"),
    align: "center" as const,
  },
]);

const rows = computed<Record<string, unknown>[]>(() =>
  libraries.value.map((library) => ({
    id: library.id,
    name: library.name || library.provider_type,
    provider_type: library.provider_type,
    enabled: library.enabled,
  })),
);

function editRow(row: Record<string, unknown>) {
  navigateToDetail(String(row.id));
}

async function loadProviders() {
  try {
    providers.value =
      (isAdmin.value
        ? await listAdminProviders()
        : await listUserProviders()) ?? [];
  } catch {
    providers.value = [];
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const offset = (page.value - 1) * perPage;
    const params = { limit: perPage, offset };
    const result = (isAdmin.value
      ? await adminListExternalLibraries(params)
      : await listUserExternalLibraries(params)) ?? { libraries: [], total: 0 };
    libraries.value = result.libraries;
    total.value = result.total;
  } catch (err) {
    error.value = t("pages.externalLibraries.loadError", {
      message:
        getApiErrorMessage(err) ||
        (err instanceof Error ? err.message : t("errors.unknown")),
    });
  } finally {
    loading.value = false;
  }
}

function navigateToNew() {
  void router.push(`${basePath.value}/new`);
}

function navigateToDetail(id: string) {
  void router.push(`${basePath.value}/${id}`);
}

function formatEnabled(enabled: boolean): string {
  return enabled ? t("common.yes") : t("common.no");
}

watch(page, () => load());
onMounted(() => {
  void loadProviders();
  void load();
});
</script>

<template>
  <div class="external-libraries-view">
    <div class="external-libraries-view__header">
      <AppPageTitle icon="cloud">
        {{
          isAdmin
            ? t("pages.externalLibraries.adminTitle")
            : t("pages.externalLibraries.listTitle")
        }}
      </AppPageTitle>
      <AppButton v-if="providers.length" icon="plus" @click="navigateToNew">
        {{ t("pages.externalLibraries.newTitle") }}
      </AppButton>
    </div>

    <div
      v-if="loading && !libraries.length"
      class="external-libraries-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="error" class="external-libraries-view__error" role="alert">
      <span>{{ error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="load">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else>
      <p v-if="!providers.length" class="external-libraries-view__empty">
        {{ t("pages.externalLibraries.noProviders") }}
      </p>
      <p v-else-if="!libraries.length" class="external-libraries-view__empty">
        {{ t("pages.externalLibraries.noLibraries") }}
      </p>

      <AppTable
        v-else
        :columns="columns"
        :rows="rows"
        :row-key="(row) => String(row.id)"
        :loading="loading"
      >
        <template #row-name="{ row, value }">
          <button
            type="button"
            class="external-libraries-view__name-btn"
            @click="editRow(row)"
          >
            {{ value }}
          </button>
        </template>

        <template #row-enabled="{ value }">
          {{ formatEnabled(Boolean(value)) }}
        </template>

        <template #row-actions="{ row }">
          <AppButton
            variant="ghost"
            size="sm"
            icon="pen-to-square"
            :aria-label="t('common.edit')"
            @click="editRow(row)"
          />
        </template>
      </AppTable>

      <AppPagination
        v-if="total > perPage"
        v-model:page="page"
        :total="total"
        :per-page="perPage"
      />
    </template>
  </div>
</template>

<style scoped>
.external-libraries-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 64rem;
}

.external-libraries-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.external-libraries-view__skeleton {
  min-height: 16rem;
}

.external-libraries-view__error,
.external-libraries-view__empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.external-libraries-view__empty {
  color: var(--color-text-muted);
}

.external-libraries-view__name-btn {
  background: transparent;
  border: none;
  color: var(--color-text);
  cursor: pointer;
  font-size: 1rem;
  padding: 0;
  text-align: left;
}

.external-libraries-view__name-btn:hover {
  color: var(--color-accent);
  text-decoration: underline;
}
</style>
