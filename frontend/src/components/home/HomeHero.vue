<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink } from "vue-router";
import { getInstanceStats, type InstanceStats } from "@/api/instance";
import { useInstanceStore } from "@/stores/instance";
import AppButton from "@/components/ui/AppButton.vue";

/**
 * Anonymous landing hero: instance name, description, optional
 * visibility-filtered stats, and sign-in/register calls to action. The
 * register CTA is shown only when the instance allows new accounts
 * (open registration or invite-only).
 */
const { t } = useI18n();
const instanceStore = useInstanceStore();

const title = computed(() => instanceStore.instance?.title || "Songhive");
const description = computed(
  () =>
    instanceStore.instance?.short_description ||
    instanceStore.instance?.description ||
    t("pages.home.hero.defaultTagline"),
);
const canRegister = computed(
  () => instanceStore.registrations || instanceStore.invitesEnabled,
);

const stats = ref<InstanceStats | null>(null);

const statLabels = computed(() => {
  const s = stats.value;
  if (!s) return [];
  return (
    [
      ["tracks", s.tracks],
      ["albums", s.albums],
      ["artists", s.artists],
      ["libraries", s.libraries],
      ["users", s.users],
    ] as const
  ).map(([key, count]) => t(`pages.home.hero.stats.${key}`, { count }));
});

onMounted(async () => {
  void instanceStore.load();
  try {
    stats.value = await getInstanceStats();
  } catch {
    // Stats are gated behind ``public_stats_enabled`` — a 404 (or any
    // other failure) simply hides the numbers row.
    stats.value = null;
  }
});
</script>

<template>
  <section class="home-hero">
    <h1 class="home-hero__title">{{ title }}</h1>
    <p class="home-hero__description">{{ description }}</p>

    <ul v-if="statLabels.length" class="home-hero__stats">
      <li v-for="label in statLabels" :key="label">{{ label }}</li>
    </ul>

    <div class="home-hero__cta">
      <RouterLink v-slot="{ navigate }" to="/login" custom>
        <AppButton icon="right-to-bracket" @click="navigate">
          {{ t("auth.login") }}
        </AppButton>
      </RouterLink>
      <RouterLink
        v-if="canRegister"
        v-slot="{ navigate }"
        to="/register"
        custom
      >
        <AppButton variant="secondary" icon="user-plus" @click="navigate">
          {{ t("auth.register") }}
        </AppButton>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.home-hero {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-6);
  border-radius: var(--radius-lg);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
}

.home-hero__title {
  margin: 0;
  font-size: 1.75rem;
}

.home-hero__description {
  margin: 0;
  color: var(--color-text-secondary);
  max-width: 48rem;
}

.home-hero__stats {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.home-hero__cta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-2);
}
</style>
