import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import * as librariesApi from "@/api/libraries";
import type { LibraryResponse, LibraryUpdate } from "@/api/libraries";
import type { TrackResponse } from "@/api/tracks";
import LibraryEditView from "./LibraryEditView.vue";

vi.mock("@/api/libraries", () => ({
  getLibrary: vi.fn(),
  updateLibrary: vi.fn(),
  deleteLibrary: vi.fn(),
  listLibraryTracks: vi.fn(),
  uploadTrack: vi.fn(),
  bulkUploadTracks: vi.fn(),
  scanLibrary: vi.fn(),
}));

vi.mock("@/api/tracks", () => ({
  listTracks: vi.fn(),
  deleteTrack: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/libraries/:id", component: { template: "<div/>" } },
      { path: "/libraries/:id/edit", component: { template: "<div/>" } },
      { path: "/libraries", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
    ],
  });
}

function createLibrary(
  id: string,
  name: string,
  ownerId = "user-1",
): LibraryResponse {
  return {
    id,
    name,
    owner_id: ownerId,
    description: "Main music library.",
    visibility: "public",
    can_write: true,
  };
}

function createTrack(id: string, title: string): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 1,
    disc_number: null,
    duration: 185,
    genre: null,
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    owner_id: "user-1",
    artist: { id: "artist-1", name: "The Larks", image_url: null },
    album: {
      id: "album-1",
      title: "Meadowland",
      artist_id: "artist-1",
      artist: null,
      musicbrainz_id: null,
      release_year: 2024,
      cover_url: null,
      owner_id: "user-1",
      visibility: "public",
    },
  };
}

function setFiles(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, "files", {
    value: files,
    configurable: true,
  });
}

function setAuthenticated(userId = "user-1") {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
  authStore.user = { id: userId, username: "alice" } as never;
}

describe("LibraryEditView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(librariesApi.getLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library"),
    );
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([]);
    vi.mocked(librariesApi.updateLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library Updated"),
    );
    vi.mocked(librariesApi.deleteLibrary).mockResolvedValue(undefined);
    vi.mocked(librariesApi.uploadTrack).mockResolvedValue({});
    vi.mocked(librariesApi.bulkUploadTracks).mockResolvedValue({});
    vi.mocked(librariesApi.scanLibrary).mockResolvedValue({});
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(LibraryEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
    return router;
  }

  it("loads the library form for the owner", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/libraries/library-1/edit");

    expect(librariesApi.getLibrary).toHaveBeenCalledWith("library-1");
    expect(wrapper.text()).toContain("Edit library");

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    const descriptionInput = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    expect(nameInput.value).toBe("Main Library");
    expect(descriptionInput.value).toBe("Main music library.");
    expect(router.currentRoute.value.path).toBe("/libraries/library-1/edit");
  });

  it("redirects non-owners to the library detail page", async () => {
    setAuthenticated("user-2");
    const router = await mountAt("/libraries/library-1/edit");

    expect(librariesApi.getLibrary).toHaveBeenCalledWith("library-1");
    expect(router.currentRoute.value.path).toBe("/libraries/library-1");
  });

  it("submits the metadata form with the correct body", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/libraries/library-1/edit");

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    const descriptionInput = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    const visibilityInputs = document.body.querySelectorAll("select");
    // First select is metadata visibility; the second is upload visibility.
    const visibilityInput = visibilityInputs[0] as HTMLSelectElement;

    nameInput.value = "Main Library Updated";
    nameInput.dispatchEvent(new Event("input"));
    descriptionInput.value = "Updated description.";
    descriptionInput.dispatchEvent(new Event("input"));
    visibilityInput.value = "private";
    visibilityInput.dispatchEvent(new Event("change"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    const expectedBody: LibraryUpdate = {
      name: "Main Library Updated",
      description: "Updated description.",
      visibility: "private",
    };
    expect(librariesApi.updateLibrary).toHaveBeenCalledWith(
      "library-1",
      expectedBody,
    );
    expect(router.currentRoute.value.path).toBe("/libraries/library-1");
  });

  it("deletes the library after confirmation and navigates to the list", async () => {
    setAuthenticated("user-1");
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);

    const router = await mountAt("/libraries/library-1/edit");

    const deleteButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    expect(deleteButton).toBeDefined();
    await deleteButton?.click();
    await flushPromises();

    expect(confirm.open).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("Main Library"),
      }),
    );
    expect(librariesApi.deleteLibrary).toHaveBeenCalledWith("library-1");
    expect(router.currentRoute.value.path).toBe("/libraries");
  });

  it("uploads a single track file with options", async () => {
    setAuthenticated("user-1");
    await mountAt("/libraries/library-1/edit");

    const visibilityInputs = document.body.querySelectorAll("select");
    const uploadVisibilityInput = visibilityInputs[1] as HTMLSelectElement;
    uploadVisibilityInput.value = "public";
    uploadVisibilityInput.dispatchEvent(new Event("change"));

    const fileInput = document.body.querySelectorAll(
      '.library-edit-view__file-input[type="file"]',
    )[0] as HTMLInputElement;
    const file = new File(["audio"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(librariesApi.uploadTrack).toHaveBeenCalledWith(
      "library-1",
      file,
      expect.objectContaining({
        visibility: "public",
        force: false,
        enrich: true,
      }),
    );
  });

  it("bulk-uploads track files with options", async () => {
    setAuthenticated("user-1");
    await mountAt("/libraries/library-1/edit");

    const fileInput = document.body.querySelectorAll(
      '.library-edit-view__file-input[type="file"]',
    )[1] as HTMLInputElement;
    const file1 = new File(["audio1"], "song1.mp3", { type: "audio/mpeg" });
    const file2 = new File(["audio2"], "song2.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file1, file2]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(librariesApi.bulkUploadTracks).toHaveBeenCalledWith(
      "library-1",
      [file1, file2],
      expect.objectContaining({
        force: false,
        enrich: true,
      }),
    );
  });

  it("triggers a library scan", async () => {
    setAuthenticated("user-1");
    await mountAt("/libraries/library-1/edit");

    const textInputs = document.body.querySelectorAll('input[type="text"]');
    const scanPathInput = textInputs[1] as HTMLInputElement;

    scanPathInput.value = "/music/scan";
    scanPathInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const scanButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("browse.libraryManagement.scan"),
    );
    expect(scanButton).toBeDefined();
    await scanButton?.click();
    await flushPromises();

    expect(librariesApi.scanLibrary).toHaveBeenCalledWith("library-1", {
      path: "/music/scan",
    });
  });

  it("displays artist and album metadata from included track summaries", async () => {
    setAuthenticated("user-1");
    vi.mocked(librariesApi.listLibraryTracks).mockResolvedValue([
      createTrack("track-1", "Song One"),
    ]);

    await mountAt("/libraries/library-1/edit");
    await flushPromises();

    expect(wrapper.text()).toContain("Song One");
    expect(wrapper.text()).toContain("The Larks");
    expect(wrapper.text()).toContain("Meadowland");
  });

  it("shows an error banner with a retry button", async () => {
    setAuthenticated("user-1");
    vi.mocked(librariesApi.getLibrary).mockRejectedValue(
      new Error("not found"),
    );

    const router = await mountAt("/libraries/library-1/edit");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(librariesApi.getLibrary).mockResolvedValue(
      createLibrary("library-1", "Main Library"),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    expect(nameInput.value).toBe("Main Library");
    expect(router.currentRoute.value.path).toBe("/libraries/library-1/edit");
  });

  it("surfaces a single-upload error", async () => {
    setAuthenticated("user-1");
    vi.mocked(librariesApi.uploadTrack).mockRejectedValue(
      new Error("upload failed"),
    );

    await mountAt("/libraries/library-1/edit");

    const fileInput = document.body.querySelectorAll(
      '.library-edit-view__file-input[type="file"]',
    )[0] as HTMLInputElement;
    const file = new File(["audio"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(document.body.textContent).toContain("upload failed");
  });

  it("surfaces a scan error", async () => {
    setAuthenticated("user-1");
    vi.mocked(librariesApi.scanLibrary).mockRejectedValue(
      new Error("scan failed"),
    );

    await mountAt("/libraries/library-1/edit");

    const textInputs = document.body.querySelectorAll('input[type="text"]');
    const scanPathInput = textInputs[1] as HTMLInputElement;

    scanPathInput.value = "/bad/path";
    scanPathInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const scanButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find(
      (b) => b.textContent === i18n.global.t("browse.libraryManagement.scan"),
    );
    expect(scanButton).toBeDefined();
    await scanButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("scan failed");
  });

  it("uploads a single track with force and enrich toggled", async () => {
    setAuthenticated("user-1");
    await mountAt("/libraries/library-1/edit");

    const checkboxes = document.body.querySelectorAll('input[type="checkbox"]');
    const forceCheckbox = checkboxes[0] as HTMLInputElement;
    const enrichCheckbox = checkboxes[1] as HTMLInputElement;

    forceCheckbox.checked = true;
    forceCheckbox.dispatchEvent(new Event("change"));
    enrichCheckbox.checked = false;
    enrichCheckbox.dispatchEvent(new Event("change"));
    await flushPromises();

    const fileInput = document.body.querySelectorAll(
      '.library-edit-view__file-input[type="file"]',
    )[0] as HTMLInputElement;
    const file = new File(["audio"], "song.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(librariesApi.uploadTrack).toHaveBeenCalledWith(
      "library-1",
      file,
      expect.objectContaining({
        force: true,
        enrich: false,
      }),
    );
  });

  it("bulk-uploads track files with force and enrich toggled", async () => {
    setAuthenticated("user-1");
    await mountAt("/libraries/library-1/edit");

    const checkboxes = document.body.querySelectorAll('input[type="checkbox"]');
    const forceCheckbox = checkboxes[0] as HTMLInputElement;
    const enrichCheckbox = checkboxes[1] as HTMLInputElement;

    forceCheckbox.checked = true;
    forceCheckbox.dispatchEvent(new Event("change"));
    enrichCheckbox.checked = false;
    enrichCheckbox.dispatchEvent(new Event("change"));
    await flushPromises();

    const fileInput = document.body.querySelectorAll(
      '.library-edit-view__file-input[type="file"]',
    )[1] as HTMLInputElement;
    const file1 = new File(["audio1"], "song1.mp3", { type: "audio/mpeg" });
    const file2 = new File(["audio2"], "song2.mp3", { type: "audio/mpeg" });
    setFiles(fileInput, [file1, file2]);
    fileInput.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(librariesApi.bulkUploadTracks).toHaveBeenCalledWith(
      "library-1",
      [file1, file2],
      expect.objectContaining({
        force: true,
        enrich: false,
      }),
    );
  });
});
