<script setup lang="ts">
import { computed } from "vue";
import ProviderIcon from "@/components/external-libraries/ProviderIcon.vue";
import { providerDisplayName } from "@/utils/providerName";

export interface Props {
  provider?: string | null;
  isExternal?: boolean;
}

const props = defineProps<Props>();

const PROVIDER_BADGE_LABELS: Record<string, string> = {
  local: "Local storage",
  s3: "Amazon S3",
};

const badge = computed(() => {
  const provider = props.provider?.toLowerCase() ?? "";
  if (!provider && !props.isExternal) return null;
  return {
    provider: provider || null,
    label:
      PROVIDER_BADGE_LABELS[provider] ??
      (provider ? providerDisplayName(provider) : "External"),
  };
});
</script>

<template>
  <span
    v-if="badge"
    class="external-track-badge"
    :title="badge.label"
    :aria-label="badge.label"
    role="img"
  >
    <ProviderIcon :provider="badge.provider" />
  </span>
</template>

<style scoped>
.external-track-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: calc(0.5 * var(--space-1));
  padding: calc(1.5 * var(--space-1));
  color: var(--color-text-muted);
  font-size: 0.75rem;
}
</style>
