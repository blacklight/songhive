import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as downloadsApi from "@/api/downloads";
import type { DownloadArchive } from "@/api/downloads";
import DownloadsView from "./DownloadsView.vue";

vi.mock("@/api/downloads", () => ({
  listArchives: vi.fn(),
  deleteArchive: vi.fn(),
  clearCompletedArchives: vi.fn(),
  downloadArchiveFile: vi.fn(),
}));

const listArchives = vi.mocked(downloadsApi.listArchives);
const deleteArchive = vi.mocked(downloadsApi.deleteArchive);
const clearCompleted = vi.mocked(downloadsApi.clearCompletedArchives);
const downloadFile = vi.mocked(downloadsApi.downloadArchiveFile);

const confirmMock = vi.fn();
vi.mock("@/composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

function makeArchive(
  overrides: Partial<DownloadArchive> = {},
): DownloadArchive {
  return {
    id: `arch-${Math.random().toString(36).slice(2, 8)}`,
    status: "ready",
    label: "My Mix",
    item_count: 3,
    items: [],
    download_url: "/api/v1/downloads/x/file",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function mountView() {
  return mount(DownloadsView, {
    global: {
      plugins: [i18n],
    },
  });
}

describe("DownloadsView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    listArchives.mockReset();
    deleteArchive.mockReset();
    clearCompleted.mockReset();
    downloadFile.mockReset();
    confirmMock.mockReset();
  });

  it("shows the empty state when there are no archives", async () => {
    listArchives.mockResolvedValue([]);
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain("No downloads yet");
  });

  it("lists archives with their status", async () => {
    listArchives.mockResolvedValue([
      makeArchive({ label: "Ready One", status: "ready" }),
      makeArchive({ label: "Failed One", status: "failed", error: "boom" }),
      makeArchive({ label: "Queued One", status: "pending" }),
    ]);
    const wrapper = mountView();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Ready One");
    expect(text).toContain("Failed One");
    expect(text).toContain("Queued One");
    expect(text).toContain("boom");
  });

  it("only shows a download button for ready archives", async () => {
    listArchives.mockResolvedValue([
      makeArchive({ label: "Ready One", status: "ready" }),
      makeArchive({ label: "Queued One", status: "pending" }),
    ]);
    const wrapper = mountView();
    await flushPromises();
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".downloads-view__row");
    expect(rows).toHaveLength(2);
    const downloadButtons = rows
      .map((row) =>
        row
          .findAll("button")
          .find((btn) => btn.text() === i18n.global.t("common.download")),
      )
      .filter(Boolean);
    expect(downloadButtons).toHaveLength(1);
  });

  it("downloads a ready archive", async () => {
    const archive = makeArchive({ status: "ready" });
    listArchives.mockResolvedValue([archive]);
    downloadFile.mockResolvedValue(undefined);

    const wrapper = mountView();
    await flushPromises();

    const button = wrapper
      .findAll("button")
      .find((btn) => btn.text() === i18n.global.t("common.download"));
    await button!.trigger("click");
    await flushPromises();
    expect(downloadFile).toHaveBeenCalledWith(
      expect.objectContaining({ id: archive.id }),
    );
  });

  it("deletes an archive", async () => {
    const archive = makeArchive();
    listArchives.mockResolvedValue([archive]);
    deleteArchive.mockResolvedValue(undefined);

    const wrapper = mountView();
    await flushPromises();

    const button = wrapper
      .findAll("button")
      .find((btn) => btn.text() === i18n.global.t("common.delete"));
    await button!.trigger("click");
    await flushPromises();
    expect(deleteArchive).toHaveBeenCalledWith(archive.id);
    expect(wrapper.text()).not.toContain("My Mix");
  });

  it("clears completed archives after confirmation", async () => {
    listArchives.mockResolvedValue([makeArchive({ status: "ready" })]);
    confirmMock.mockResolvedValue(true);
    clearCompleted.mockResolvedValue({ cleared: 1 });
    listArchives.mockResolvedValueOnce([makeArchive({ status: "ready" })]);

    const wrapper = mountView();
    await flushPromises();

    const button = wrapper
      .findAll("button")
      .find((btn) => btn.text() === i18n.global.t("downloads.clearCompleted"));
    expect(button).toBeDefined();
    await button!.trigger("click");
    await flushPromises();

    expect(confirmMock).toHaveBeenCalled();
    expect(clearCompleted).toHaveBeenCalled();
  });

  it("does not clear when the confirmation is declined", async () => {
    listArchives.mockResolvedValue([makeArchive({ status: "ready" })]);
    confirmMock.mockResolvedValue(false);

    const wrapper = mountView();
    await flushPromises();

    const button = wrapper
      .findAll("button")
      .find((btn) => btn.text() === i18n.global.t("downloads.clearCompleted"));
    await button!.trigger("click");
    await flushPromises();

    expect(clearCompleted).not.toHaveBeenCalled();
  });
});
