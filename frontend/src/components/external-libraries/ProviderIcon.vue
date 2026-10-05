<script setup lang="ts">
import { computed } from "vue";

export interface Props {
  provider?: string | null;
}

const props = defineProps<Props>();

// Brand marks that FontAwesome Free ships — resolved to fa-brands classes.
const BRAND_ICONS: Record<string, string> = {
  tidal: "fa-tidal",
  youtube: "fa-youtube",
  gdrive: "fa-google-drive",
  dropbox: "fa-dropbox",
  s3: "fa-aws",
};

const SOLID_ICONS: Record<string, string> = {
  local: "fa-hard-drive",
};

const iconClass = computed(() => {
  const key = props.provider?.toLowerCase() ?? "";
  if (BRAND_ICONS[key]) return `fa-brands ${BRAND_ICONS[key]}`;
  return `fa-solid ${SOLID_ICONS[key] ?? "fa-cloud"}`;
});

const isJellyfin = computed(() => props.provider?.toLowerCase() === "jellyfin");
</script>

<template>
  <!-- Jellyfin has no FontAwesome glyph; path from Simple Icons (CC0). -->
  <svg
    v-if="isJellyfin"
    class="provider-icon"
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
  >
    <path
      d="M12 .002C8.826.002-1.398 18.537.16 21.666c1.56 3.129 22.14 3.094 23.682 0C25.384 18.573 15.177 0 12 0zm7.76 18.949c-1.008 2.028-14.493 2.05-15.514 0C3.224 16.9 9.92 4.755 12.003 4.755c2.081 0 8.77 12.166 7.759 14.196zM12 9.198c-1.054 0-4.446 6.15-3.93 7.189.518 1.04 7.348 1.027 7.86 0 .511-1.027-2.874-7.19-3.93-7.19z"
    />
  </svg>
  <i v-else :class="iconClass" aria-hidden="true" />
</template>

<style scoped>
.provider-icon {
  width: 1em;
  height: 1em;
  vertical-align: -0.125em;
}
</style>
