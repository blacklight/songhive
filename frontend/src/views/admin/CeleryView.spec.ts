import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import CeleryView from "./CeleryView.vue";

vi.mock("@/api/admin", () => ({
  listCeleryTasks: vi.fn(),
  terminateCeleryTasks: vi.fn(),
}));

vi.mock("@/composables/useConfirm", () => ({
  useConfirm: vi.fn(),
}));

import { useConfirm } from "@/composables/useConfirm";

describe("CeleryView", () => {
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

  const sampleTask = {
    task_id: "task-1",
    name: "songhive.tasks.storage.cleanup_orphaned_files",
    worker: "worker1@host",
    args: [],
    kwargs: {},
    runtime: 1.234,
    hostname: "worker1@host",
    acknowledged: true,
    delivery_info: { exchange: "", routing_key: "celery" },
    time_start: 1_700_000_000.0,
  };

  it("renders a list of running Celery tasks", async () => {
    vi.mocked(adminApi.listCeleryTasks).mockResolvedValue([sampleTask]);

    wrapper = mount(CeleryView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.listCeleryTasks).toHaveBeenCalled();
    expect(wrapper.text()).toContain(sampleTask.task_id);
    expect(wrapper.text()).toContain(sampleTask.name);
    expect(wrapper.text()).toContain(sampleTask.worker);
  });

  it("shows an empty message when no tasks are running", async () => {
    vi.mocked(adminApi.listCeleryTasks).mockResolvedValue([]);

    wrapper = mount(CeleryView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("pages.admin.celery.empty"));
  });

  it("shows an error toast when loading fails", async () => {
    vi.mocked(adminApi.listCeleryTasks).mockRejectedValue(new Error("boom"));

    wrapper = mount(CeleryView, { global: { plugins: [i18n] } });
    await flushPromises();

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("error");
  });

  it("refreshes the task list when the refresh button is clicked", async () => {
    vi.mocked(adminApi.listCeleryTasks).mockResolvedValue([sampleTask]);

    wrapper = mount(CeleryView, { global: { plugins: [i18n] } });
    await flushPromises();

    vi.mocked(adminApi.listCeleryTasks).mockClear();

    const buttons = wrapper.findAll("button");
    const refreshButton = buttons.find(
      (b) => b.text() === i18n.global.t("pages.admin.celery.refresh"),
    );
    await refreshButton?.trigger("click");
    await flushPromises();

    expect(adminApi.listCeleryTasks).toHaveBeenCalled();
  });

  it("terminates a single task and reloads the list", async () => {
    vi.mocked(adminApi.listCeleryTasks).mockResolvedValue([sampleTask]);
    vi.mocked(adminApi.terminateCeleryTasks).mockResolvedValue({
      terminated: 1,
    });

    wrapper = mount(CeleryView, { global: { plugins: [i18n] } });
    await flushPromises();

    const buttons = wrapper.findAll("button");
    const terminateButton = buttons.find(
      (b) => b.text() === i18n.global.t("pages.admin.celery.terminate"),
    );
    await terminateButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.terminateCeleryTasks).toHaveBeenCalledWith({
      task_ids: [sampleTask.task_id],
    });

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].type).toBe("success");
  });

  it("terminates selected tasks in bulk", async () => {
    const tasks = [
      sampleTask,
      {
        ...sampleTask,
        task_id: "task-2",
        name: "songhive.tasks.tags.sync_track_tags",
      },
    ];
    vi.mocked(adminApi.listCeleryTasks).mockResolvedValue(tasks);
    vi.mocked(adminApi.terminateCeleryTasks).mockResolvedValue({
      terminated: 2,
    });

    wrapper = mount(CeleryView, { global: { plugins: [i18n] } });
    await flushPromises();

    const checkboxes = wrapper.findAll("input[type='checkbox']");
    // Select the header "select all" checkbox (first) to select every row.
    const selectAll = checkboxes[0];
    await selectAll.setValue(true);
    await flushPromises();

    const buttons = wrapper.findAll("button");
    const bulkButton = buttons.find(
      (b) =>
        b.text() ===
        i18n.global.t("pages.admin.celery.terminateSelected", { count: 2 }),
    );
    await bulkButton?.trigger("click");
    await flushPromises();

    expect(adminApi.terminateCeleryTasks).toHaveBeenCalledWith({
      task_ids: expect.arrayContaining(["task-1", "task-2"]),
    });
  });
});
