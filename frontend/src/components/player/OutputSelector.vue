<script setup lang="ts">
import { computed, onMounted, ref, useTemplateRef } from "vue";
import { useI18n } from "vue-i18n";
import { useOutputsStore } from "@/stores/outputs";
import { usePlaybackStore } from "@/stores/playback";
import AppButton from "@/components/ui/AppButton.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import ContextMenu, { type MenuItem } from "@/components/ui/ContextMenu.vue";
import type { OutputResponse } from "@/api/outputs";

const { t } = useI18n();
const outputStore = useOutputsStore();
const playbackStore = usePlaybackStore();

const loading = computed(() => playbackStore.loading || outputStore.loading);
const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);
const rootRef = useTemplateRef<HTMLElement>("root");

const outputs = computed(() => [
  outputStore.webOutput,
  ...outputStore.enabledOutputs,
]);

const activeOutputId = computed(() => playbackStore.activeOutputId || "web");

const activeOutput = computed(
  () =>
    outputs.value.find((output) => output.id === activeOutputId.value) ??
    outputStore.webOutput,
);

const menuItems = computed<MenuItem[]>(() =>
  outputs.value.map((output) => ({
    key: output.id,
    label: outputDisplayName(output),
    icon: outputIcon(output),
    active: output.id === activeOutputId.value,
  })),
);

const currentError = computed(() => {
  const output = outputStore.getOutput(playbackStore.activeOutputId || "");
  return output?.last_error || playbackStore.error || outputStore.error;
});

const buttonTitle = computed(() => {
  const base = `${t("outputs.select")}: ${outputDisplayName(activeOutput.value)}`;
  return currentError.value ? `${base} — ${currentError.value}` : base;
});

function outputIcon(output: OutputResponse): string {
  switch (output.provider_type) {
    case "web":
      return "display";
    case "icecast":
      return "tower-broadcast";
    default:
      return "server";
  }
}

function outputDisplayName(output: OutputResponse): string {
  if (output.provider_type === "web") {
    return t("outputs.thisDevice");
  }
  const provider = outputStore.getProvider(output.provider_type);
  const prefix = provider ? provider.provider_type : output.provider_type;
  return `${prefix}: ${output.name}`;
}

function toggleMenu() {
  if (menuOpen.value) {
    menuOpen.value = false;
    return;
  }
  const rect = rootRef.value?.getBoundingClientRect();
  // Anchored to the trigger's top-right; the menu flips up/left when it
  // would overflow the viewport.
  menuX.value = rect ? Math.round(rect.right) : 0;
  menuY.value = rect ? Math.round(rect.top) : 0;
  menuOpen.value = true;
}

function closeMenu() {
  menuOpen.value = false;
}

async function onMenuSelect(key: string) {
  closeMenu();
  if (key === activeOutputId.value) return;
  try {
    await playbackStore.selectOutput(key);
  } catch {
    // Error is stored in playbackStore.error.
  }
}

onMounted(async () => {
  playbackStore.registerWithPlayer();
  await outputStore.loadProviders();
  await outputStore.loadOutputs();
  if (!playbackStore.activeOutputId) {
    playbackStore.setActiveOutput("web");
  }
});
</script>

<template>
  <div ref="root" class="output-selector">
    <AppButton
      variant="ghost"
      size="sm"
      class="output-selector__button"
      :class="{ 'output-selector__button--error': currentError }"
      :icon="outputIcon(activeOutput)"
      :title="buttonTitle"
      :aria-label="t('outputs.select')"
      :disabled="loading"
      aria-haspopup="menu"
      :aria-expanded="menuOpen"
      @click="toggleMenu"
    />
    <ContextMenu
      :open="menuOpen"
      :items="menuItems"
      :x="menuX"
      :y="menuY"
      :trigger="rootRef"
      @select="onMenuSelect"
      @close="closeMenu"
    >
      <template v-if="currentError" #header>
        <div class="output-selector__error" role="alert">
          <AppIcon
            name="triangle-exclamation"
            class="output-selector__error-icon"
          />
          <span class="output-selector__error-text">{{ currentError }}</span>
        </div>
      </template>
    </ContextMenu>
  </div>
</template>

<style scoped>
.output-selector {
  display: flex;
  align-items: center;
}

.output-selector .output-selector__button {
  font-size: 1.25rem;
}

.output-selector__button.output-selector__button--error {
  color: var(--color-danger);
}

.output-selector__error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  max-width: 16rem;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.output-selector__error-icon {
  flex-shrink: 0;
  margin-top: 0.125rem;
}
</style>
