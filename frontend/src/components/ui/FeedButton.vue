<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import AppButton from "./AppButton.vue";
import ContextMenu, { type MenuItem } from "./ContextMenu.vue";
import type { FeedUrls } from "@/utils/feeds";

export interface Props {
  urls: FeedUrls;
  size?: "sm" | "md" | "lg";
}

const props = withDefaults(defineProps<Props>(), {
  size: "sm",
});

const { t } = useI18n();

const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);

const menuItems = computed<MenuItem[]>(() => [
  { key: "rss", label: t("feeds.rss"), icon: "rss" },
  { key: "atom", label: t("feeds.atom"), icon: "rss" },
]);

function openMenu(event: MouseEvent) {
  const trigger = event.currentTarget as HTMLElement | null;
  if (trigger) {
    const rect = trigger.getBoundingClientRect();
    menuX.value = Math.round(rect.right);
    menuY.value = Math.round(rect.bottom);
  } else {
    menuX.value = event.clientX;
    menuY.value = event.clientY;
  }
  menuOpen.value = true;
}

function closeMenu() {
  menuOpen.value = false;
}

function onMenuSelect(key: string) {
  closeMenu();
  const url = key === "atom" ? props.urls.atom : props.urls.rss;
  window.open(url, "_blank", "noopener");
}
</script>

<template>
  <AppButton
    :size="props.size"
    icon="rss"
    variant="secondary"
    :title="t('feeds.subscribe')"
    :aria-label="t('feeds.subscribe')"
    class="feed-button"
    @click="openMenu"
  >
    {{ t("feeds.subscribe") }}
  </AppButton>
  <ContextMenu
    :open="menuOpen"
    :items="menuItems"
    :x="menuX"
    :y="menuY"
    @select="onMenuSelect"
    @close="closeMenu"
  />
</template>
