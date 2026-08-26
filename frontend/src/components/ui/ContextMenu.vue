<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
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
const adjustedX = ref(props.x ?? 0);
const adjustedY = ref(props.y ?? 0);
const isPositioned = ref(false);

const style = computed(() => ({
  position: "fixed" as const,
  top: `${adjustedY.value}px`,
  left: `${adjustedX.value}px`,
  zIndex: "var(--z-dropdown)",
  visibility: isPositioned.value ? ("visible" as const) : ("hidden" as const),
}));

useOnClickOutside(
  () => menuRef.value,
  () => emit("close"),
);

function reposition() {
  adjustedX.value = props.x ?? 0;
  adjustedY.value = props.y ?? 0;
  isPositioned.value = false;

  nextTick(() => {
    updatePosition();
  });
}

function updatePosition() {
  const element = menuRef.value;
  if (!element) return;

  const rect = element.getBoundingClientRect();
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;

  let x = props.x ?? 0;
  let y = props.y ?? 0;

  if (x + rect.width > viewportWidth) {
    x -= rect.width;
  }
  if (y + rect.height > viewportHeight) {
    y -= rect.height;
  }

  adjustedX.value = Math.max(0, x);
  adjustedY.value = Math.max(0, y);
  isPositioned.value = true;
}

function onResize() {
  if (!props.open) return;
  reposition();
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      activeIndex.value = -1;
      isPositioned.value = false;
      return;
    }

    activeIndex.value = 0;
    nextTickFocus();
    reposition();
  },
  { immediate: true },
);

watch([() => props.x, () => props.y], () => {
  if (!props.open) return;
  reposition();
});

onMounted(() => {
  window.addEventListener("resize", onResize);
});

onUnmounted(() => {
  window.removeEventListener("resize", onResize);
});

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
  max-height: calc(100vh - 2 * var(--space-2));
  overflow-y: auto;
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

.context-menu__item:focus {
  background-color: var(--color-surface-raised);
}

.context-menu__item:hover {
  background-color: var(--color-surface-hover);
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
