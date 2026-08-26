import { nextTick, onBeforeUnmount, ref, watch } from "vue";

const FOCUSABLE_SELECTORS = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "textarea:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
];

export function useFocusTrap(
  open: () => boolean,
  dialogRef: () => HTMLElement | null,
) {
  const previousActive = ref<Element | null>(null);
  let boundElement: HTMLElement | null = null;

  function getFocusables(): HTMLElement[] {
    const root = dialogRef();
    if (!root) return [];
    const elements = Array.from(
      root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS.join(",")),
    );
    return elements.filter((el) => el.offsetParent !== null);
  }

  function focusFirst() {
    const focusables = getFocusables();
    if (focusables.length > 0) {
      focusables[0].focus();
    } else {
      const root = dialogRef();
      if (
        root &&
        "focus" in root &&
        typeof (root as HTMLElement).focus === "function"
      ) {
        (root as HTMLElement).setAttribute("tabindex", "-1");
        (root as HTMLElement).focus();
      }
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== "Tab" || !open()) return;

    const focusables = getFocusables();
    if (focusables.length === 0) {
      event.preventDefault();
      return;
    }

    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function onOpen() {
    if (typeof document === "undefined") return;
    previousActive.value = document.activeElement;
    boundElement = dialogRef();
    boundElement?.addEventListener("keydown", onKeyDown);
    document.body.style.overflow = "hidden";
    nextTick(() => focusFirst());
  }

  function onClose() {
    boundElement?.removeEventListener("keydown", onKeyDown);
    document.body.style.overflow = "";
    if (previousActive.value && "focus" in previousActive.value) {
      (previousActive.value as HTMLElement).focus();
    }
    previousActive.value = null;
    boundElement = null;
  }

  watch(
    open,
    (isOpen) => {
      if (isOpen) onOpen();
      else onClose();
    },
    { flush: "post" },
  );

  onBeforeUnmount(() => {
    onClose();
  });
}
