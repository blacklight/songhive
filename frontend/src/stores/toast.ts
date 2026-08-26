import { defineStore } from "pinia";
import { ref, type Ref } from "vue";

export type ToastType = "success" | "error" | "info" | "warning";

export interface Toast {
  id: string;
  type: ToastType;
  message: string;
  timeout?: number;
}

const MAX_TOASTS = 5;

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const useToastStore = defineStore("toast", () => {
  const toasts: Ref<Toast[]> = ref([]);

  function push(payload: {
    type: ToastType;
    message: string;
    timeout?: number;
  }): string {
    const id = generateId();
    const timeout = payload.timeout ?? 5000;
    const toast: Toast = { id, ...payload, timeout };

    toasts.value.push(toast);
    if (toasts.value.length > MAX_TOASTS) {
      toasts.value.shift();
    }

    if (toast.timeout && toast.timeout > 0) {
      setTimeout(() => dismiss(id), toast.timeout);
    }

    return id;
  }

  function dismiss(id: string) {
    const index = toasts.value.findIndex((t) => t.id === id);
    if (index !== -1) {
      toasts.value.splice(index, 1);
    }
  }

  function clear() {
    toasts.value = [];
  }

  return { toasts, push, dismiss, clear };
});
