import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useConfirm } from "./useConfirm";
import { useConfirmStore } from "@/stores/confirm";

describe("useConfirm", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("resolves true on confirm", () => {
    const store = useConfirmStore();
    const { confirm } = useConfirm();
    const promise = confirm({ message: "Sure?" });
    store.confirm();
    return expect(promise).resolves.toBe(true);
  });

  it("resolves false on cancel", () => {
    const store = useConfirmStore();
    const { confirm } = useConfirm();
    const promise = confirm({ message: "Sure?" });
    store.cancel();
    return expect(promise).resolves.toBe(false);
  });
});
