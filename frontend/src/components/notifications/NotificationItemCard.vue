<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import AppIcon from "@/components/ui/AppIcon.vue";
import { getItemSummary, type ItemSummary } from "@/composables/useItemSummary";

const props = defineProps<{
  itemType: string;
  itemId: string;
  title?: string | null;
  to: string;
}>();

const TYPE_ICONS: Record<string, string> = {
  track: "music",
  album: "compact-disc",
  artist: "microphone",
  playlist: "list",
  library: "folder-open",
  radio: "tower-broadcast",
  file: "file",
};

const summary = ref<ItemSummary | null>(null);

const label = computed(() => props.title || summary.value?.title || "");
const imageUrl = computed(() => summary.value?.imageUrl ?? null);
const icon = computed(() => TYPE_ICONS[props.itemType] ?? "link");

onMounted(() => {
  void getItemSummary(props.itemType, props.itemId).then((result) => {
    summary.value = result;
  });
});
</script>

<template>
  <RouterLink :to="to" class="item-card">
    <span class="item-card__art">
      <img v-if="imageUrl" :src="imageUrl" alt="" loading="lazy" />
      <AppIcon v-else :name="icon" />
    </span>
    <span class="item-card__title">{{ label || itemId }}</span>
    <AppIcon name="chevron-right" class="item-card__chevron" />
  </RouterLink>
</template>

<style scoped>
.item-card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  text-decoration: none;
}

.item-card:hover {
  border-color: var(--color-accent);
}

.item-card__art {
  width: 2.5rem;
  height: 2.5rem;
  border-radius: var(--radius-sm);
  overflow: hidden;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.item-card__art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.item-card__title {
  flex: 1;
  min-width: 0;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-card__chevron {
  color: var(--color-text-muted);
  flex-shrink: 0;
}
</style>
