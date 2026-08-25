import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { uploadFile, type StoredFileResponse } from "@/api/files";
import { useToastStore } from "@/stores/toast";
import FilesView from "./FilesView.vue";

vi.mock("@/api/files", () => ({
  uploadFile: vi.fn(),
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

function setFiles(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, "files", {
    value: files,
    configurable: true,
  });
}

function createStoredFile(id: string): StoredFileResponse {
  return {
    id,
    content_type: "image/png",
    size: 1536,
    sha256: "abc123",
    owner_id: "user-1",
    visibility: "public",
    original_filename: "avatar.png",
    url: "/api/v1/files/f1/download",
  };
}

describe("FilesView", () => {
  let wrapper: ReturnType<typeof mount>;
  let router: ReturnType<typeof createTestRouter>;
  let toast: ReturnType<typeof useToastStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountView() {
    router = createTestRouter();
    await router.push("/");
    await router.isReady();
    vi.spyOn(router, "push").mockResolvedValue(undefined);
    toast = useToastStore();
    wrapper = mount(FilesView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("renders the upload form and notice", async () => {
    await mountView();

    expect(wrapper.text()).toContain(i18n.global.t("pages.files.title"));
    expect(wrapper.text()).toContain(i18n.global.t("pages.files.noList"));
    expect(wrapper.text()).toContain(i18n.global.t("pages.files.selectFile"));
    expect(wrapper.find('input[type="file"]').exists()).toBe(true);
  });

  it("uploads a file with the selected visibility", async () => {
    vi.mocked(uploadFile).mockResolvedValue(createStoredFile("f1"));

    await mountView();

    const select = wrapper.find("select").element as HTMLSelectElement;
    select.value = "private";
    select.dispatchEvent(new Event("change"));
    await flushPromises();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(uploadFile).toHaveBeenCalledWith(
      file,
      "private",
      expect.any(Function),
    );
    expect(router.push).toHaveBeenCalledWith({
      name: "file",
      params: { id: "f1" },
    });
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("success");
    expect(toast.toasts[0].message).toBe(
      i18n.global.t("pages.files.uploadSuccess"),
    );
  });

  it("updates the progress bar while uploading", async () => {
    let finishUpload!: (value: StoredFileResponse) => void;

    vi.mocked(uploadFile).mockImplementation(
      async (_file, _visibility, onProgress) => {
        onProgress?.(50);
        return new Promise<StoredFileResponse>((resolve) => {
          finishUpload = resolve;
        });
      },
    );

    await mountView();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    const bar = wrapper.find(".files-view__progress-bar")
      .element as HTMLDivElement;
    expect(bar.style.width).toBe("50%");
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.files.uploadProgress", { percent: 50 }),
    );

    finishUpload(createStoredFile("f1"));
    await flushPromises();

    expect(router.push).toHaveBeenCalledWith({
      name: "file",
      params: { id: "f1" },
    });
  });

  it("shows an error when the upload fails", async () => {
    vi.mocked(uploadFile).mockRejectedValue(new Error("network failure"));

    await mountView();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("pages.files.uploadError", {
        message: "network failure",
      }),
    );
    expect(router.push).not.toHaveBeenCalled();
  });

  it("resets the input value after an upload so the same file can be re-picked", async () => {
    vi.mocked(uploadFile).mockRejectedValue(new Error("network failure"));

    await mountView();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(fileInput.value).toBe("");
  });
});
