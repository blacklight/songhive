import { onBeforeUnmount, onMounted } from "vue";

export function useOnClickOutside(
  element: () => HTMLElement | null,
  handler: (event: MouseEvent) => void,
) {
  function onClick(event: MouseEvent) {
    const el = element();
    if (el && !el.contains(event.target as Node)) {
      handler(event);
    }
  }

  onMounted(() => {
    document.addEventListener("click", onClick, true);
  });

  onBeforeUnmount(() => {
    document.removeEventListener("click", onClick, true);
  });
}
