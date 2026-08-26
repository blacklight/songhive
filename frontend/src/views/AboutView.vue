<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { useInstanceStore } from "@/stores/instance";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";

const { t } = useI18n();
const instanceStore = useInstanceStore();

const appName = computed(
  () =>
    instanceStore.instance?.title || t("pages.about.defaultName") || "Songhive",
);
const appVersion = computed(
  () =>
    instanceStore.instance?.songhive_version ||
    import.meta.env.PACKAGE_VERSION ||
    "0.0.1",
);
const appDescription = computed(
  () =>
    instanceStore.instance?.description ||
    instanceStore.instance?.short_description ||
    t("pages.about.defaultDescription"),
);
const docsUrl = computed(() => import.meta.env.VITE_DOCS_URL);
const supportUrl = computed(() => import.meta.env.VITE_SUPPORT_URL);
const hasLinks = computed(() => Boolean(docsUrl.value || supportUrl.value));
const errorMessage = computed(() => instanceStore.error || t("errors.unknown"));

onMounted(() => void instanceStore.load());
</script>

<template>
  <main class="about-view">
    <AppPageTitle class="about-view__title" icon="circle-info">{{
      t("pages.about.title")
    }}</AppPageTitle>

    <section class="about-view__card" :aria-label="t('pages.about.title')">
      <div v-if="instanceStore.loading" class="about-view__loading">
        {{ t("common.loading") }}
      </div>
      <template v-else>
        <div v-if="instanceStore.status === 'error'" class="about-view__error">
          {{ errorMessage }}
        </div>
        <dl class="about-view__list">
          <div class="about-view__row">
            <dt>{{ t("pages.about.instanceName") }}</dt>
            <dd>{{ appName }}</dd>
          </div>
          <div class="about-view__row">
            <dt>{{ t("pages.about.version") }}</dt>
            <dd>{{ appVersion }}</dd>
          </div>
          <div v-if="appDescription" class="about-view__row">
            <dt>{{ t("pages.about.description") }}</dt>
            <dd>{{ appDescription }}</dd>
          </div>
        </dl>

        <div v-if="hasLinks" class="about-view__links">
          <a
            v-if="docsUrl"
            :href="docsUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="about-view__link"
          >
            {{ t("pages.about.documentation") }}
          </a>
          <a
            v-if="supportUrl"
            :href="supportUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="about-view__link"
          >
            {{ t("pages.about.support") }}
          </a>
        </div>
      </template>
    </section>
  </main>
</template>

<style scoped>
.about-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 40rem;
}

.about-view__title {
  margin: 0;
  font-size: 1.5rem;
}

.about-view__card {
  padding: var(--space-6);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
}

.about-view__list {
  margin: 0;
  display: grid;
  gap: var(--space-4);
}

.about-view__row {
  display: grid;
  grid-template-columns: 10rem 1fr;
  gap: var(--space-4);
  align-items: baseline;
}

.about-view__row dt {
  color: var(--color-text-muted);
  font-weight: 500;
}

.about-view__row dd {
  margin: 0;
  color: var(--color-text);
}

.about-view__links {
  margin-top: var(--space-6);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.about-view__link {
  display: inline-block;
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-accent);
  color: var(--color-accent-contrast);
  text-decoration: none;
  font-weight: 500;
  transition: filter var(--transition-fast);
}

.about-view__link:hover {
  filter: brightness(0.95);
}

.about-view__loading,
.about-view__error {
  color: var(--color-text-muted);
}

.about-view__error {
  color: var(--color-error);
}

@media (max-width: 767px) {
  .about-view__row {
    grid-template-columns: 1fr;
    gap: var(--space-1);
  }
}
</style>
