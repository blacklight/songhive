<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import AppButton from "./AppButton.vue";
import ContextMenu, { type MenuItem } from "./ContextMenu.vue";

export type ActionVariant = "primary" | "secondary" | "ghost" | "danger";

export interface ActionItem {
  key: string;
  label: string;
  icon?: string;
  variant?: ActionVariant;
  visible?: boolean;
  disabled?: boolean;
  loading?: boolean;
}

interface Props {
  actions: ActionItem[];
  primaryCount?: number;
  size?: "sm" | "md" | "lg";
}

const props = withDefaults(defineProps<Props>(), {
  primaryCount: 2,
  size: "sm",
});

const emit = defineEmits<{ select: [key: string] }>();

const { t } = useI18n();

const visibleActions = computed(() =>
  props.actions.filter((action) => action.visible !== false),
);

const primaryActions = computed(() =>
  visibleActions.value.slice(0, props.primaryCount),
);

const menuActions = computed(() =>
  visibleActions.value.slice(props.primaryCount),
);

const menuOpen = ref(false);
const menuX = ref(0);
const menuY = ref(0);

const menuItems = computed<MenuItem[]>(() =>
  menuActions.value.map((action) => ({
    key: action.key,
    label: action.label,
    icon: action.icon,
    danger: action.variant === "danger",
  })),
);

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
  emit("select", key);
}
</script>

<template>
  <div class="entity-actions">
    <AppButton
      v-for="action in primaryActions"
      :key="action.key"
      :size="props.size"
      :icon="action.icon"
      :variant="action.variant"
      :disabled="action.disabled"
      :loading="action.loading"
      class="entity-actions__item entity-actions__item--primary"
      @click="emit('select', action.key)"
    >
      {{ action.label }}
    </AppButton>

    <AppButton
      v-for="action in menuActions"
      :key="action.key"
      :size="props.size"
      :icon="action.icon"
      :variant="action.variant"
      :disabled="action.disabled"
      :loading="action.loading"
      class="entity-actions__item entity-actions__item--menu"
      @click="emit('select', action.key)"
    >
      {{ action.label }}
    </AppButton>

    <AppButton
      v-if="menuItems.length > 0"
      :size="props.size"
      icon="ellipsis"
      :title="t('common.openMenu')"
      :aria-label="t('common.openMenu')"
      class="entity-actions__more"
      @click="openMenu"
    />

    <ContextMenu
      :open="menuOpen"
      :items="menuItems"
      :x="menuX"
      :y="menuY"
      @select="onMenuSelect"
      @close="closeMenu"
    />
  </div>
</template>

<style scoped>
.entity-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
}

.entity-actions__more {
  display: none;
}

@media (max-width: 767px) {
  .entity-actions__item--menu {
    display: none;
  }

  .entity-actions__more {
    display: inline-flex;
  }
}
</style>
