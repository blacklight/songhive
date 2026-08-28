import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { listFiles, uploadFile, type StoredFileResponse } from "@/api/files";
import { listLibraries, type LibraryResponse } from "@/api/libraries";
import { useToastStore } from "@/stores/toast";
import FilesView from "./FilesView.vue";

vi.mock("@/api/files", () => ({
  listFiles: vi.fn(),
  uploadFile: vi.fn(),
  deleteFile: vi.fn(),
}));

vi.mock("@/api/libraries", () => ({
  listLibraries: vi.fn(),
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
      {
        path: "/tracks/:id",
        name: "track",
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

function createStoredFile(
  id: string,
  overrides?: Partial<StoredFileResponse>,
): StoredFileResponse {
  return {
    id,
    content_type: "image/png",
    size: 1536,
    sha256: "abc123",
    owner_id: "user-1",
    visibility: "public",
    original_filename: "avatar.png",
    url: `/api/v1/files/${id}/download`,
    ...overrides,
  };
}

function createLibrary(
  id: string,
  name: string,
  canWrite = true,
): LibraryResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: null,
    visibility: "public",
    can_write: canWrite,
  };
}

describe("FilesView", () => {
  let wrapper: ReturnType<typeof mount>;
  let router: ReturnType<typeof createTestRouter>;
  let toast: ReturnType<typeof useToastStore>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(listLibraries).mockResolvedValue([
      createLibrary("lib1", "Uploads"),
      createLibrary("lib2", "My Library"),
    ]);
    vi.mocked(listFiles).mockResolvedValue([]);
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

  it("renders the upload form and an empty list notice", async () => {
    await mountView();

    expect(wrapper.text()).toContain(i18n.global.t("pages.files.title"));
    expect(wrapper.text()).toContain(i18n.global.t("pages.files.selectFile"));
    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.files"),
      }),
    );
    expect(wrapper.find('input[type="file"]').exists()).toBe(true);
  });

  it("loads files on mount", async () => {
    vi.mocked(listFiles).mockResolvedValue([
      createStoredFile("f1"),
      createStoredFile("f2"),
    ]);

    await mountView();

    expect(listFiles).toHaveBeenCalledWith(
      expect.objectContaining({ limit: expect.any(Number), offset: 0 }),
    );
    expect(wrapper.text()).toContain("avatar.png");
    expect(wrapper.findAll(".files-view__item")).toHaveLength(2);
  });

  it("shows an error when listing files fails", async () => {
    vi.mocked(listFiles).mockRejectedValue(new Error("network failure"));

    await mountView();

    expect(wrapper.text()).toContain("network failure");
  });

  it("uploads a file with the selected visibility", async () => {
    vi.mocked(uploadFile).mockResolvedValue(createStoredFile("f1"));

    await mountView();

    const selects = wrapper.findAll("select");
    const visibilitySelect = selects.find(
      (s) => (s.element as HTMLSelectElement).options[0]?.value === "private",
    )?.element as HTMLSelectElement | undefined;
    expect(visibilitySelect).toBeDefined();
    visibilitySelect!.value = "private";
    visibilitySelect!.dispatchEvent(new Event("change"));
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
      undefined,
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

  it("uploads a file to the selected library", async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      ...createStoredFile("f1"),
      trackId: "t1",
    });

    await mountView();
    await flushPromises();

    const selects = wrapper.findAll("select");
    const librarySelect = selects.find(
      (s) => (s.element as HTMLSelectElement).options[0]?.value === "",
    )?.element as HTMLSelectElement | undefined;
    expect(librarySelect).toBeDefined();
    librarySelect!.value = "lib2";
    librarySelect!.dispatchEvent(new Event("change"));
    await flushPromises();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(uploadFile).toHaveBeenCalledWith(
      file,
      "public",
      expect.any(Function),
      "lib2",
    );
    expect(router.push).toHaveBeenCalledWith({
      name: "track",
      params: { id: "t1" },
    });
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

  it("navigates to the track when the upload returns a trackId", async () => {
    vi.mocked(uploadFile).mockResolvedValue({
      ...createStoredFile("f1"),
      trackId: "t1",
    });

    await mountView();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file = new File(["contents"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(router.push).toHaveBeenCalledWith({
      name: "track",
      params: { id: "t1" },
    });
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

  it("bulk-uploads multiple files and refreshes the list", async () => {
    vi.mocked(uploadFile)
      .mockResolvedValueOnce(createStoredFile("f1"))
      .mockResolvedValueOnce(createStoredFile("f2"));

    await mountView();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file1 = new File(["a"], "song1.mp3", { type: "audio/mpeg" });
    const file2 = new File(["b"], "song2.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file1, file2]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(uploadFile).toHaveBeenNthCalledWith(
      1,
      file1,
      "public",
      expect.any(Function),
      undefined,
    );
    expect(uploadFile).toHaveBeenNthCalledWith(
      2,
      file2,
      "public",
      expect.any(Function),
      undefined,
    );
    expect(router.push).not.toHaveBeenCalled();
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("success");
    expect(toast.toasts[0].message).toBe(
      i18n.global.t("pages.files.uploadSuccessPlural", { count: 2 }),
    );
  });

  it("shows per-file errors for a partially failed bulk upload", async () => {
    vi.mocked(uploadFile)
      .mockResolvedValueOnce(createStoredFile("f1"))
      .mockRejectedValueOnce(new Error("network failure"));

    await mountView();

    const fileInput = wrapper.find('input[type="file"]')
      .element as HTMLInputElement;
    const file1 = new File(["a"], "song1.mp3", { type: "audio/mpeg" });
    const file2 = new File(["b"], "song2.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file1, file2]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(router.push).not.toHaveBeenCalled();
    expect(toast.toasts).toHaveLength(1);
    expect(toast.toasts[0].type).toBe("warning");
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.files.uploadPartial", {
        success: 1,
        total: 2,
      }),
    );
    expect(wrapper.text()).toContain(
      i18n.global.t("pages.files.fileUploadError", {
        name: file2.name,
        message: "network failure",
      }),
    );
  });

  it("renders image thumbnails, play buttons and icons in the list", async () => {
    vi.mocked(listFiles).mockResolvedValue([
      createStoredFile("f1", {
        content_type: "image/png",
        original_filename: "avatar.png",
      }),
      createStoredFile("f2", {
        content_type: "audio/mpeg",
        original_filename: "song.mp3",
      }),
      createStoredFile("f3", {
        content_type: "text/plain",
        original_filename: "notes.txt",
      }),
    ]);

    await mountView();

    const images = wrapper.findAll("img");
    expect(images).toHaveLength(1);
    expect(images[0].attributes("src")).toBe(
      "/api/v1/files/f1/download?disposition=inline",
    );

    const playButtons = wrapper.findAll(".files-view__play");
    expect(playButtons).toHaveLength(1);
    const playIcon = playButtons[0].find("i");
    expect(playIcon.classes()).toContain("fa-play");

    const fileIcons = wrapper.findAll(".files-view__icon");
    expect(fileIcons).toHaveLength(1);
  });

  it("toggles the audio preview play button", async () => {
    vi.mocked(listFiles).mockResolvedValue([
      createStoredFile("f1", {
        content_type: "audio/mpeg",
        original_filename: "song.mp3",
      }),
    ]);

    await mountView();

    const button = wrapper.find(".files-view__play");
    expect(button.find("i").classes()).toContain("fa-play");

    await button.trigger("click");
    await flushPromises();

    expect(button.find("i").classes()).toContain("fa-pause");

    await button.trigger("click");
    await flushPromises();

    expect(button.find("i").classes()).toContain("fa-play");
  });
});
