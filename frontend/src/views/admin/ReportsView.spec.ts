import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import type { ReportResponse } from "@/api/admin";
import ReportsView from "./ReportsView.vue";

vi.mock("@/api/admin", () => ({
  listReports: vi.fn(),
  updateReport: vi.fn(),
}));

function createReport(
  id: string,
  status: ReportResponse["status"],
): ReportResponse {
  return {
    id,
    reporter_id: "user-1",
    target_type: "track",
    target_id: "track-1",
    reason: "spam",
    description: null,
    status,
    reviewed_by: null,
    reviewed_at: null,
    resolution_notes: null,
    created_at: "2024-01-01T00:00:00Z",
  };
}

describe("ReportsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(adminApi.listReports).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("lists reports on mount", async () => {
    vi.mocked(adminApi.listReports).mockResolvedValue([
      createReport("r1", "pending"),
    ]);

    wrapper = mount(ReportsView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.listReports).toHaveBeenCalledWith({
      status: undefined,
      limit: 25,
      offset: 0,
    });
    expect(wrapper.text()).toContain("pending");
  });

  it("filters reports by status", async () => {
    vi.mocked(adminApi.listReports)
      .mockResolvedValueOnce([createReport("r1", "pending")])
      .mockResolvedValueOnce([createReport("r2", "resolved")]);

    wrapper = mount(ReportsView, { global: { plugins: [i18n] } });
    await flushPromises();

    const select = wrapper.find("select");
    await select.setValue("resolved");
    await flushPromises();

    expect(adminApi.listReports).toHaveBeenLastCalledWith({
      status: "resolved",
      limit: 25,
      offset: 0,
    });
  });

  it("resolves a report from the modal", async () => {
    vi.mocked(adminApi.listReports).mockResolvedValue([
      createReport("r1", "pending"),
    ]);
    vi.mocked(adminApi.updateReport).mockResolvedValue(
      createReport("r1", "resolved"),
    );

    wrapper = mount(ReportsView, {
      attachTo: document.body,
      global: { plugins: [i18n] },
    });
    await flushPromises();

    const detailsButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.reports.details"));
    await detailsButton?.trigger("click");
    await flushPromises();

    const textarea = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    textarea.value = "Resolved by admin";
    textarea.dispatchEvent(new Event("input"));
    await flushPromises();

    const resolveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("pages.admin.reports.resolve"),
    );
    await resolveButton?.click();
    await flushPromises();

    expect(adminApi.updateReport).toHaveBeenCalledWith("r1", {
      status: "resolved",
      resolution_notes: "Resolved by admin",
    });

    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("success");
  });
});
