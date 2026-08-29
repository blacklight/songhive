import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import StorageView from "./StorageView.vue";

vi.mock("@/api/admin", () => ({
  triggerStorageCleanup: vi.fn(),
}));

vi.mock("@/composables/useConfirm", () => ({
  useConfirm: vi.fn(),
}));

import { useConfirm } from "@/composables/useConfirm";

describe("StorageView", () => {
  let wrapper: ReturnType<typeof mount>;
  const confirm = vi.fn();

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    confirm.mockResolvedValue(true);
    vi.mocked(useConfirm).mockReturnValue({ confirm, store: {} as never });
  });

  afterEach(() => {
    wrapper?.unmount();
  });

  it("triggers cleanup after confirmation", async () => {
    vi.mocked(adminApi.triggerStorageCleanup).mockResolvedValue(null);

    wrapper = mount(StorageView, { global: { plugins: [i18n] } });
    await flushPromises();

    const triggerButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.storage.trigger"));
    await triggerButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.triggerStorageCleanup).toHaveBeenCalled();

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("success");
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("pages.admin.storage.triggered"),
    );
  });

  it("shows an error toast on failure", async () => {
    vi.mocked(adminApi.triggerStorageCleanup).mockRejectedValue(
      new Error("cleanup failed"),
    );

    wrapper = mount(StorageView, { global: { plugins: [i18n] } });
    await flushPromises();

    const triggerButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.storage.trigger"));
    await triggerButton?.trigger("click");
    await flushPromises();

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("error");
  });
});
