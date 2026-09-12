<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useThemeStore, type ThemeMode } from "@/stores/theme";
import AppSelect from "@/components/ui/AppSelect.vue";

const { t } = useI18n();
const themeStore = useThemeStore();

const mode = computed({
  get: () => themeStore.mode,
  set: (value: string) => themeStore.setMode(value as ThemeMode),
});

const themeOptions = computed(() => [
  { value: "system", label: t("theme.system") },
  { value: "light", label: t("theme.light") },
  { value: "dark", label: t("theme.dark") },
]);
</script>

<template>
  <section class="interface-tab">
    <h2 class="interface-tab__title">{{ t("profile.interface.title") }}</h2>
    <p class="interface-tab__hint">{{ t("profile.interface.hint") }}</p>

    <AppSelect
      v-model="mode"
      class="interface-tab__theme"
      :options="themeOptions"
      :label="t('profile.interface.themeLabel')"
      :hint="t('profile.interface.themeHint')"
    />
  </section>
</template>

<style scoped>
.interface-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.interface-tab__title {
  margin: 0;
  font-size: 1.25rem;
}

.interface-tab__hint {
  margin: 0;
  color: var(--color-text-muted);
}

.interface-tab__theme {
  max-width: 20rem;
}
</style>
