import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import TasksView from "./TasksView.vue";

vi.mock("@/api/admin", () => ({
  triggerStorageCleanup: vi.fn(),
  syncTags: vi.fn(),
  rehashAudio: vi.fn(),
  provisionFederationKeys: vi.fn(),
}));

vi.mock("@/composables/useConfirm", () => ({
  useConfirm: vi.fn(),
}));

import { useConfirm } from "@/composables/useConfirm";

describe("TasksView", () => {
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

  it("triggers storage cleanup after confirmation", async () => {
    vi.mocked(adminApi.triggerStorageCleanup).mockResolvedValue(null);

    wrapper = mount(TasksView, { global: { plugins: [i18n] } });
    await flushPromises();

    const buttons = wrapper.findAll("button");
    const triggerButton = buttons.find(
      (b) =>
        b.text() === i18n.global.t("pages.admin.tasks.storageCleanup.trigger"),
    );
    await triggerButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.triggerStorageCleanup).toHaveBeenCalled();

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("success");
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("pages.admin.tasks.storageCleanup.triggered"),
    );
  });

  it("triggers a tag sync for all tracks", async () => {
    vi.mocked(adminApi.syncTags).mockResolvedValue({
      enqueued: 5,
      status: "queued",
    });

    wrapper = mount(TasksView, { global: { plugins: [i18n] } });
    await flushPromises();

    const buttons = wrapper.findAll("button");
    const syncButton = buttons.find(
      (b) => b.text() === i18n.global.t("pages.admin.tasks.syncTags.trigger"),
    );
    await syncButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.syncTags).toHaveBeenCalledWith(
      expect.objectContaining({ all: true, dry_run: false }),
    );

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("pages.admin.tasks.syncTags.triggered", { count: 5 }),
    );
  });

  it("shows an error toast on task failure", async () => {
    vi.mocked(adminApi.triggerStorageCleanup).mockRejectedValue(
      new Error("cleanup failed"),
    );

    wrapper = mount(TasksView, { global: { plugins: [i18n] } });
    await flushPromises();

    const buttons = wrapper.findAll("button");
    const triggerButton = buttons.find(
      (b) =>
        b.text() === i18n.global.t("pages.admin.tasks.storageCleanup.trigger"),
    );
    await triggerButton?.trigger("click");
    await flushPromises();

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("error");
  });
});
