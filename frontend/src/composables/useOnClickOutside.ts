import { onBeforeUnmount, onMounted } from "vue";

export function useOnClickOutside(
  element: () => HTMLElement | null,
  handler: () => void,
) {
  function onClick(event: MouseEvent) {
    const el = element();
    if (el && !el.contains(event.target as Node)) {
      handler();
    }
  }

  onMounted(() => {
    document.addEventListener("click", onClick, true);
  });

  onBeforeUnmount(() => {
    document.removeEventListener("click", onClick, true);
  });
}
