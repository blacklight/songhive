import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useModalStackStore } from "./modalStack";

describe("useModalStackStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("assigns increasing depth as modals open", () => {
    const store = useModalStackStore();

    const first = store.open();
    const second = store.open();
    const third = store.open();

    expect(store.depthOf(first)).toBe(0);
    expect(store.depthOf(second)).toBe(1);
    expect(store.depthOf(third)).toBe(2);
    expect(store.openModals).toBe(3);
  });

  it("removes a closed modal from the stack", () => {
    const store = useModalStackStore();

    const first = store.open();
    const second = store.open();
    store.close(second);

    expect(store.depthOf(first)).toBe(0);
    expect(store.depthOf(second)).toBe(-1);
    expect(store.openModals).toBe(1);
  });

  it("does not reuse depth values for a new modal after a middle modal closes", () => {
    const store = useModalStackStore();

    const first = store.open();
    const second = store.open();
    const third = store.open();
    store.close(second);

    expect(store.depthOf(first)).toBe(0);
    expect(store.depthOf(third)).toBe(1);

    const fourth = store.open();
    expect(store.depthOf(fourth)).toBe(2);
  });
});
