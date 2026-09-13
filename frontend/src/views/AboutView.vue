<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useInstanceStore } from "@/stores/instance";
import AppAvatar from "@/components/ui/AppAvatar.vue";
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

const staffAccounts = computed(
  () => instanceStore.instance?.staff_accounts ?? [],
);
const contact = computed(() => instanceStore.instance?.contact ?? null);
const hasContactSection = computed(
  () => staffAccounts.value.length > 0 || contact.value !== null,
);

const emailRevealed = ref(false);
const maskedEmail = computed(() =>
  (contact.value?.email ?? "")
    .replace("@", " [at] ")
    .replaceAll(".", " [dot] "),
);

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
          <div class="about-view__row">
            <dt>{{ t("pages.about.projectUrl") }}</dt>
            <dd>
              <a
                href="https://git.fabiomanganiello.com/songhive"
                target="_blank"
              >
                git.fabiomanganiello.com/songhive
              </a>
            </dd>
          </div>
          <div class="about-view__row">
            <dt>{{ t("pages.about.githubUrl") }}</dt>
            <dd>
              <a href="https://github.com/blacklight/songhive" target="_blank">
                github.com/blacklight/songhive
              </a>
            </dd>
          </div>
          <div class="about-view__row">
            <dt>{{ t("pages.about.issueTracker") }}</dt>
            <dd>
              <a
                href="https://github.com/blacklight/songhive/issues"
                target="_blank"
              >
                github.com/blacklight/songhive/issues
              </a>
            </dd>
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

    <section
      v-if="hasContactSection && !instanceStore.loading"
      class="about-view__card"
      :aria-label="t('pages.about.contact')"
    >
      <h2 class="about-view__section-title">
        {{ t("pages.about.contact") }}
      </h2>

      <div v-if="staffAccounts.length" class="about-view__staff">
        <h3 class="about-view__subtitle">
          {{ t("pages.about.administrators") }}
        </h3>
        <ul class="about-view__staff-list">
          <li v-for="account in staffAccounts" :key="account.username">
            <a :href="account.url" class="about-view__staff-link">
              <AppAvatar
                :src="account.avatar_url ?? undefined"
                :name="account.display_name || account.username"
                size="md"
              />
              <span class="about-view__staff-info">
                <span class="about-view__staff-name">
                  {{ account.display_name || account.username }}
                </span>
                <span class="about-view__staff-handle">
                  @{{ account.acct }}
                </span>
              </span>
            </a>
          </li>
        </ul>
      </div>

      <div v-if="contact" class="about-view__contact-person">
        <h3 class="about-view__subtitle">
          {{ t("pages.about.contactPerson") }}
        </h3>
        <dl class="about-view__list">
          <div v-if="contact.name" class="about-view__row">
            <dt>{{ t("pages.about.contactName") }}</dt>
            <dd>{{ contact.name }}</dd>
          </div>
          <div v-if="contact.email" class="about-view__row">
            <dt>{{ t("pages.about.contactEmail") }}</dt>
            <dd>
              <a v-if="emailRevealed" :href="`mailto:${contact.email}`">
                {{ contact.email }}
              </a>
              <button
                v-else
                type="button"
                class="about-view__masked-email"
                :title="t('pages.about.revealEmail')"
                @click="emailRevealed = true"
              >
                {{ maskedEmail }}
              </button>
            </dd>
          </div>
          <div v-if="contact.url" class="about-view__row">
            <dt>{{ t("pages.about.contactUrl") }}</dt>
            <dd>
              <a :href="contact.url" target="_blank" rel="noopener noreferrer">
                {{ contact.url }}
              </a>
            </dd>
          </div>
        </dl>
      </div>
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

.about-view__section-title {
  margin: 0 0 var(--space-4);
  font-size: 1.125rem;
}

.about-view__subtitle {
  margin: 0 0 var(--space-3);
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.about-view__staff-list {
  margin: 0 0 var(--space-4);
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.about-view__staff-link {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  border-bottom: none;
}

.about-view__staff-info {
  display: flex;
  flex-direction: column;
}

.about-view__staff-name {
  color: var(--color-text);
  font-weight: 500;
}

.about-view__staff-handle {
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.about-view__masked-email {
  padding: 0;
  border: none;
  background: none;
  color: var(--color-text-muted);
  font: inherit;
  cursor: pointer;
  border-bottom: 1px dotted var(--color-text-muted);
}

@media (max-width: 767px) {
  .about-view__row {
    grid-template-columns: 1fr;
    gap: var(--space-1);
  }
}

a {
  color: var(--color-text-muted);
  text-decoration: none;
  border-bottom: 1px dotted var(--color-text-muted);
}
</style>
