<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppTabs from "@/components/ui/AppTabs.vue";
import SharesCreatedPanel from "@/components/share/SharesCreatedPanel.vue";
import SharesReceivedPanel from "@/components/share/SharesReceivedPanel.vue";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();

// ``/shares`` defaults to created shares; ``?tab=received`` deep-links the
// "Shared with me" panel.
const activeTab = computed(() =>
  route.query.tab === "received" ? "received" : "created",
);

const tabs = computed(() => [
  { value: "created", label: t("pages.shares.tabs.created") },
  { value: "received", label: t("pages.shares.tabs.received") },
]);

function onTab(value: string) {
  router.replace({
    query: value === "received" ? { tab: "received" } : {},
  });
}
</script>

<template>
  <div class="shares-view">
    <div class="shares-view__header">
      <AppPageTitle icon="share-nodes">{{
        t("pages.shares.title")
      }}</AppPageTitle>
    </div>

    <AppTabs
      :model-value="activeTab"
      :tabs="tabs"
      @update:model-value="onTab"
    />

    <SharesCreatedPanel v-if="activeTab === 'created'" />
    <SharesReceivedPanel v-else />
  </div>
</template>

<style scoped>
.shares-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
</style>
