import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useToastStore } from "./toast";

describe("useToastStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  it("push adds a toast", () => {
    const store = useToastStore();
    store.push({ type: "info", message: "hello" });
    expect(store.toasts.length).toBe(1);
    expect(store.toasts[0].message).toBe("hello");
  });

  it("auto-removes after timeout", () => {
    const store = useToastStore();
    store.push({ type: "info", message: "hello", timeout: 100 });
    vi.advanceTimersByTime(100);
    expect(store.toasts.length).toBe(0);
  });

  it("prunes to max 5", () => {
    const store = useToastStore();
    for (let i = 0; i < 7; i++) {
      store.push({ type: "info", message: `toast ${i}` });
    }
    expect(store.toasts.length).toBe(5);
    expect(store.toasts[0].message).toBe("toast 2");
  });
});
