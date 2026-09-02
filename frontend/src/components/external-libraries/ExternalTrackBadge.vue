<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "@/components/ui/AppIcon.vue";

export interface Props {
  provider?: string | null;
  isExternal?: boolean;
}

const props = defineProps<Props>();

interface IconDescriptor {
  name: string;
  variant?: "solid" | "brand";
  label: string;
}

const providerIconMap: Record<string, IconDescriptor> = {
  local: { name: "hard-drive", label: "Local storage" },
  s3: { name: "aws", variant: "brand", label: "Amazon S3" },
};

function formatLabel(provider: string): string {
  return provider ? provider[0].toUpperCase() + provider.slice(1) : "External";
}

const badge = computed<IconDescriptor | null>(() => {
  const provider = props.provider?.toLowerCase() ?? "";
  if (provider && providerIconMap[provider]) {
    return providerIconMap[provider];
  }
  if (props.isExternal || provider) {
    return {
      name: "cloud",
      label: provider ? formatLabel(provider) : "External",
    };
  }
  return null;
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
    <AppIcon :name="badge.name" :variant="badge.variant" />
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
