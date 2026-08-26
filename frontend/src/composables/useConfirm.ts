import { useConfirmStore, type ConfirmOptions } from "@/stores/confirm";

export function useConfirm() {
  const store = useConfirmStore();

  function confirm(options: ConfirmOptions): Promise<boolean> {
    return store.open(options);
  }

  return { confirm, store };
}
