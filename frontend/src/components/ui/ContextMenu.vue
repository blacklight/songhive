<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useOnClickOutside } from "@/composables/useOnClickOutside";
import AppIcon from "./AppIcon.vue";

export interface MenuItem {
  key: string;
  label: string;
  icon?: string;
  iconVariant?: "solid" | "regular" | "light" | "brand";
  danger?: boolean;
}

export interface Props {
  items: MenuItem[];
  open: boolean;
  x?: number;
  y?: number;
}

const props = defineProps<Props>();
const emit = defineEmits<{ select: [key: string]; close: [] }>();

const menuRef = ref<HTMLElement | null>(null);
const activeIndex = ref(-1);

const style = computed(() => ({
  position: "fixed" as const,
  top: `${props.y ?? 0}px`,
  left: `${props.x ?? 0}px`,
  zIndex: "var(--z-dropdown)",
}));

useOnClickOutside(
  () => menuRef.value,
  () => emit("close"),
);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      activeIndex.value = -1;
      return;
    }
    activeIndex.value = 0;
    nextTickFocus();
  },
);

function nextTickFocus() {
  setTimeout(() => {
    const items = getItemElements();
    items[0]?.focus();
  }, 0);
}

function getItemElements(): HTMLElement[] {
  if (!menuRef.value) return [];
  return Array.from(menuRef.value.querySelectorAll('[role="menuitem"]'));
}

function onKeyDown(event: KeyboardEvent) {
  const items = getItemElements();
  if (!items.length) return;

  if (event.key === "Escape") {
    emit("close");
    return;
  }

  if (event.key === "ArrowDown") {
    event.preventDefault();
    activeIndex.value = (activeIndex.value + 1) % items.length;
    items[activeIndex.value]?.focus();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    activeIndex.value =
      activeIndex.value <= 0 ? items.length - 1 : activeIndex.value - 1;
    items[activeIndex.value]?.focus();
  } else if (event.key === "Enter" && activeIndex.value >= 0) {
    event.preventDefault();
    emit("select", props.items[activeIndex.value].key);
  }
}

function select(key: string) {
  emit("select", key);
}
</script>

<template>
  <Teleport to="body">
    <ul
      v-if="props.open"
      ref="menuRef"
      role="menu"
      class="context-menu"
      :style="style"
      @keydown="onKeyDown"
    >
      <li
        v-for="item in props.items"
        :key="item.key"
        role="menuitem"
        :class="[
          'context-menu__item',
          { 'context-menu__item--danger': item.danger },
        ]"
        tabindex="-1"
        @click="select(item.key)"
        @keydown.enter.space.prevent="select(item.key)"
      >
        <AppIcon
          v-if="item.icon"
          :name="item.icon"
          :variant="item.iconVariant || 'solid'"
          class="context-menu__icon"
        />
        <span class="context-menu__label">{{ item.label }}</span>
      </li>
    </ul>
  </Teleport>
</template>

<style scoped>
.context-menu {
  list-style: none;
  margin: 0;
  padding: var(--space-1);
  min-width: 10rem;
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  color: var(--color-text);
  outline: none;
}

.context-menu__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  cursor: pointer;
  outline: none;
}

.context-menu__item:hover,
.context-menu__item:focus {
  background-color: var(--color-surface-raised);
}

.context-menu__item--danger {
  color: var(--color-danger);
}

.context-menu__icon {
  display: inline-block;
  width: 1rem;
  text-align: center;
}
</style>
