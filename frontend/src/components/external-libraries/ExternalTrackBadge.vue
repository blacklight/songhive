<script setup lang="ts">
import { useI18n } from "vue-i18n";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  provider?: string | null;
  state?: string | null;
  isExternal?: boolean;
}

const props = defineProps<Props>();
const { t } = useI18n();

const provider = props.provider ?? "";
</script>

<template>
  <span v-if="isExternal || provider" class="external-track-badge">
    <AppIcon name="cloud" />
    <span class="external-track-badge__text">
      {{
        t("pages.externalLibraries.trackExternalBadge", {
          provider: provider || t("pages.externalLibraries.external"),
        })
      }}
    </span>
    <span v-if="state" class="external-track-badge__state">
      {{ t("pages.externalLibraries.trackExternalState", { state }) }}
    </span>
  </span>
</template>

<style scoped>
.external-track-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0.125rem var(--space-2);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
  font-size: 0.75rem;
  font-weight: 500;
}

.external-track-badge__state {
  text-transform: capitalize;
}
</style>
