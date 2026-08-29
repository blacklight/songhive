import { onMounted, onUnmounted, ref, type Ref } from "vue";

/**
 * React to a CSS media query.
 *
 * Defaults to `defaultValue` when `matchMedia` is unavailable (e.g. during
 * SSR or unit tests). The returned ref updates automatically when the browser
 * viewport crosses the query boundary.
 */
export function useMediaQuery(
  query: string,
  defaultValue = false,
): Ref<boolean> {
  const matches = ref(defaultValue);
  let mql: MediaQueryList | undefined;

  function update(event: Event) {
    const list = event.target as MediaQueryList;
    matches.value = list.matches;
  }

  onMounted(() => {
    if (typeof window === "undefined" || !window.matchMedia) {
      return;
    }

    mql = window.matchMedia(query);
    matches.value = mql.matches;
    mql.addEventListener("change", update);
  });

  onUnmounted(() => {
    mql?.removeEventListener("change", update);
  });

  return matches;
}
