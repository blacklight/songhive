<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { ACCENT_PRESETS, useThemeStore, type ThemeMode } from "@/stores/theme";
import AppIcon from "@/components/ui/AppIcon.vue";
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

function accentLabel(name: string): string {
  return t(`theme.accents.${name}`);
}
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

    <div class="interface-tab__accent">
      <span id="interface-accent-label" class="interface-tab__label">
        {{ t("theme.accent") }}
      </span>
      <div
        class="interface-tab__swatches"
        role="radiogroup"
        aria-labelledby="interface-accent-label"
      >
        <button
          v-for="preset in ACCENT_PRESETS"
          :key="preset.value"
          type="button"
          role="radio"
          class="interface-tab__swatch"
          :class="{
            'interface-tab__swatch--active': themeStore.accent === preset.value,
          }"
          :style="{ backgroundColor: preset.value }"
          :aria-checked="themeStore.accent === preset.value"
          :aria-label="accentLabel(preset.name)"
          :title="accentLabel(preset.name)"
          @click="themeStore.setAccent(preset.value)"
        >
          <AppIcon
            v-if="themeStore.accent === preset.value"
            name="check"
            class="interface-tab__swatch-check"
          />
        </button>
      </div>
      <p class="interface-tab__field-hint">
        {{ t("profile.interface.accentHint") }}
      </p>
    </div>
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

.interface-tab__accent {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.interface-tab__label {
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 500;
}

.interface-tab__swatches {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.interface-tab__swatch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
}

.interface-tab__swatch:focus-visible {
  outline: 2px solid var(--color-text);
  outline-offset: 2px;
}

.interface-tab__swatch--active {
  box-shadow:
    0 0 0 2px var(--color-surface),
    0 0 0 4px var(--color-text);
}

.interface-tab__swatch-check {
  color: #1f2937;
  font-size: 0.875rem;
}

.interface-tab__field-hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}
</style>
