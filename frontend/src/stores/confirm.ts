import { defineStore } from "pinia";
import { ref, type Ref } from "vue";

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

export interface ConfirmState {
  open: boolean;
  title?: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  danger: boolean;
  resolve: (value: boolean) => void;
}

export const useConfirmStore = defineStore("confirm", () => {
  const state: Ref<ConfirmState | null> = ref(null);

  function open(options: ConfirmOptions): Promise<boolean> {
    return new Promise((resolve) => {
      state.value = {
        open: true,
        title: options.title,
        message: options.message,
        confirmLabel: options.confirmLabel || "Confirm",
        cancelLabel: options.cancelLabel || "Cancel",
        danger: options.danger ?? false,
        resolve,
      };
    });
  }

  function confirm() {
    state.value?.resolve(true);
    state.value = null;
  }

  function cancel() {
    state.value?.resolve(false);
    state.value = null;
  }

  function close() {
    state.value?.resolve(false);
    state.value = null;
  }

  return { state, open, confirm, cancel, close };
});
