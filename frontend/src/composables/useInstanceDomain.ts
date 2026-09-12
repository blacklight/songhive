import { computed, type ComputedRef } from "vue";
import { useInstanceStore } from "@/stores/instance";

/**
 * The instance's host (``example.com[:port]``), used to tell local actor/tag
 * URLs from remote ones. Falls back to ``window.location.host`` until the
 * instance info has loaded.
 */
export function useInstanceDomain(): ComputedRef<string> {
  const instanceStore = useInstanceStore();
  return computed(() => {
    const uri = instanceStore.instance?.uri;
    if (uri) {
      try {
        return new URL(uri).host;
      } catch {
        // ignore
      }
    }
    return typeof window !== "undefined" ? window.location.host : "";
  });
}
