<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import AppButton from "@/components/ui/AppButton.vue";
import AppModal from "@/components/feedback/AppModal.vue";
import StatusComposer from "@/components/statuses/StatusComposer.vue";

/**
 * Authenticated home header: a time-of-day greeting plus a post action
 * that opens the shared status composer in a modal (same pattern as the
 * profile page).
 */
const emit = defineEmits<{ posted: [] }>();
const { t } = useI18n();
const authStore = useAuthStore();

const composerOpen = ref(false);

const name = computed(
  () => authStore.user?.display_name || authStore.user?.username || "",
);

const greeting = computed(() => {
  const hour = new Date().getHours();
  const key = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  return t(`pages.home.greeting.${key}`, { name: name.value });
});

function onSubmitted() {
  composerOpen.value = false;
  emit("posted");
}
</script>

<template>
  <header class="home-greeting">
    <h1 class="home-greeting__title">{{ greeting }}</h1>
    <AppButton icon="pen-to-square" @click="composerOpen = true">
      {{ t("pages.home.greeting.cta") }}
    </AppButton>

    <AppModal
      :open="composerOpen"
      :title="t('statusComposer.title')"
      @close="composerOpen = false"
    >
      <StatusComposer autofocus @submitted="onSubmitted" />
    </AppModal>
  </header>
</template>

<style scoped>
.home-greeting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.home-greeting__title {
  margin: 0;
  font-size: 1.5rem;
}
</style>
