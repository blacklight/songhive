import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { getFile, type StoredFileResponse } from "@/api/files";
import { useToastStore } from "@/stores/toast";
import FileDetailView from "./FileDetailView.vue";

vi.mock("@/api/files", () => ({
  getFile: vi.fn(),
  deleteFile: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/files/:id",
        name: "file",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createStoredFile(
  id: string,
  overrides?: Partial<StoredFileResponse>,
): StoredFileResponse {
  return {
    id,
    content_type: "image/png",
    size: 1536,
    sha256: "sha256-hash-value",
    owner_id: "user-1",
    visibility: "public",
    original_filename: "avatar.png",
    url: `/api/v1/files/${id}/download`,
    ...overrides,
  };
}

describe("FileDetailView", () => {
  let wrapper: ReturnType<typeof mount>;
  let router: ReturnType<typeof createTestRouter>;
  let toast: ReturnType<typeof useToastStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    Object.defineProperty(globalThis.navigator, "clipboard", {
      value: { writeText: vi.fn() },
      configurable: true,
    });
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(id = "f1") {
    router = createTestRouter();
    await router.push({ name: "file", params: { id } });
    await router.isReady();
    toast = useToastStore();
    wrapper = mount(FileDetailView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("renders file metadata and a download link", async () => {
    vi.mocked(getFile).mockResolvedValue(createStoredFile("f1"));
    await mountAt("f1");

    expect(getFile).toHaveBeenCalledWith("f1");
    expect(wrapper.text()).toContain("avatar.png");
    expect(wrapper.text()).toContain("image/png");
    expect(wrapper.text()).toContain("1.5 KB");
    expect(wrapper.text()).toContain("sha256-hash-value");
    expect(wrapper.text()).toContain(i18n.global.t("browse.visibility.public"));
    expect(wrapper.text()).toContain("user-1");

    const link = wrapper
      .findAll("a")
      .find((a) => a.text() === i18n.global.t("pages.files.download"));
    expect(link).toBeDefined();
    expect(link?.attributes("href")).toBe(
      "/api/v1/files/f1/download?disposition=attachment",
    );
    expect(link?.attributes("download")).toBe("avatar.png");
  });

  it("shows a loading skeleton while fetching", async () => {
    vi.mocked(getFile).mockReturnValue(new Promise(() => {}));
    await mountAt("f1");

    expect(wrapper.find(".file-detail-view__skeleton").exists()).toBe(true);
  });

  it("shows an error with a retry button when loading fails", async () => {
    vi.mocked(getFile).mockRejectedValue(new Error("not found"));
    await mountAt("f1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(getFile).mockResolvedValue(createStoredFile("f1"));

    const retry = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.retry"));
    expect(retry).toBeDefined();
    await retry?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("avatar.png");
  });

  it("omits the owner row when owner_id is null", async () => {
    vi.mocked(getFile).mockResolvedValue(
      createStoredFile("f1", { owner_id: null }),
    );
    await mountAt("f1");

    expect(wrapper.text()).not.toContain(i18n.global.t("browse.detail.owner"));
  });

  it("falls back to an untitled label when original_filename is null", async () => {
    vi.mocked(getFile).mockResolvedValue(
      createStoredFile("f1", { original_filename: null }),
    );
    await mountAt("f1");

    expect(wrapper.text()).toContain(i18n.global.t("pages.files.untitledFile"));

    const link = wrapper
      .findAll("a")
      .find((a) => a.text() === i18n.global.t("pages.files.download"));
    expect(link?.attributes("download")).toBe("f1");
  });

  it("copies the sha256 and shows a success toast", async () => {
    vi.mocked(getFile).mockResolvedValue(createStoredFile("f1"));
    vi.mocked(navigator.clipboard.writeText).mockResolvedValue(undefined);
    await mountAt("f1");

    const copy = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.copy"));
    expect(copy).toBeDefined();
    await copy?.trigger("click");
    await flushPromises();

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      "sha256-hash-value",
    );
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("success");
    expect(toast.toasts[0].message).toBe(
      i18n.global.t("pages.files.sha256Copied"),
    );
  });

  it("shows an error toast when copying the sha256 fails", async () => {
    vi.mocked(getFile).mockResolvedValue(createStoredFile("f1"));
    vi.mocked(navigator.clipboard.writeText).mockRejectedValue(
      new Error("denied"),
    );
    await mountAt("f1");

    const copy = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("common.copy"));
    await copy?.trigger("click");
    await flushPromises();

    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("error");
    expect(toast.toasts[0].message).toBe(
      i18n.global.t("pages.files.copyFailed"),
    );
  });
});
