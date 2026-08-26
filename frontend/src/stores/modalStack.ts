import { defineStore } from "pinia";
import { computed, readonly, ref } from "vue";

export const useModalStackStore = defineStore("modalStack", () => {
  const stack = ref<string[]>([]);
  const nextId = ref(0);

  const openModals = computed(() => stack.value.length);

  function open(): string {
    const id = `m-${nextId.value++}`;
    if (!stack.value.includes(id)) {
      stack.value.push(id);
    }
    return id;
  }

  function close(id: string): void {
    stack.value = stack.value.filter((modalId) => modalId !== id);
  }

  function depthOf(id: string): number {
    return stack.value.indexOf(id);
  }

  return {
    stack: readonly(stack),
    openModals,
    open,
    close,
    depthOf,
  };
});
