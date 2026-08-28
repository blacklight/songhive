import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import * as albumsApi from "@/api/albums";
import type { AlbumResponse, AlbumUpdate } from "@/api/albums";
import AlbumEditView from "./AlbumEditView.vue";

vi.mock("@/api/albums", () => ({
  getAlbum: vi.fn(),
  updateAlbum: vi.fn(),
  deleteAlbum: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/albums/:id", component: { template: "<div/>" } },
      { path: "/albums/:id/edit", component: { template: "<div/>" } },
      { path: "/albums", component: { template: "<div/>" } },
    ],
  });
}

function createAlbum(
  id: string,
  title: string,
  ownerId = "user-1",
): AlbumResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    musicbrainz_id: null,
    release_year: 2024,
    cover_url: null,
    description: "A lovely album.",
    owner_id: ownerId,
    visibility: "public",
  };
}

function setAuthenticated(userId = "user-1") {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
  authStore.user = { id: userId, username: "alice" } as never;
}

function setAdmin(userId = "admin-1") {
  setAuthenticated(userId);
  const authStore = useAuthStore();
  authStore.role = "admin";
}

describe("AlbumEditView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland"),
    );
    vi.mocked(albumsApi.updateAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland Updated"),
    );
    vi.mocked(albumsApi.deleteAlbum).mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(AlbumEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
    return router;
  }

  it("loads the album form for the owner", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/albums/album-1/edit");

    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1", {
      include: "hashtags",
    });
    expect(wrapper.text()).toContain("Edit album");

    const titleInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    const descriptionInput = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    expect(titleInput.value).toBe("Meadowland");
    expect(descriptionInput.value).toBe("A lovely album.");
    expect(router.currentRoute.value.path).toBe("/albums/album-1/edit");
  });

  it("redirects non-owners to the album detail page", async () => {
    setAuthenticated("user-2");
    const router = await mountAt("/albums/album-1/edit");

    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1", {
      include: "hashtags",
    });
    expect(router.currentRoute.value.path).toBe("/albums/album-1");
  });

  it("loads the album form for an admin who is not the owner", async () => {
    setAdmin("admin-1");
    const router = await mountAt("/albums/album-1/edit");

    expect(albumsApi.getAlbum).toHaveBeenCalledWith("album-1", {
      include: "hashtags",
    });
    expect(wrapper.text()).toContain("Edit album");
    expect(router.currentRoute.value.path).toBe("/albums/album-1/edit");
  });

  it("submits the update form with the correct body", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/albums/album-1/edit");

    const titleInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    const descriptionInput = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    const releaseYearInput = document.body.querySelector(
      'input[type="number"]',
    ) as HTMLInputElement;
    const visibilityInput = document.body.querySelector(
      "select",
    ) as HTMLSelectElement;

    titleInput.value = "Meadowland Updated";
    titleInput.dispatchEvent(new Event("input"));
    descriptionInput.value = "Updated description.";
    descriptionInput.dispatchEvent(new Event("input"));
    releaseYearInput.value = "2025";
    releaseYearInput.dispatchEvent(new Event("input"));
    visibilityInput.value = "private";
    visibilityInput.dispatchEvent(new Event("change"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    const expectedBody: AlbumUpdate = {
      title: "Meadowland Updated",
      release_year: 2025,
      description: "Updated description.",
      visibility: "private",
    };
    expect(albumsApi.updateAlbum).toHaveBeenCalledWith("album-1", expectedBody);
    expect(router.currentRoute.value.path).toBe("/albums/album-1");
  });

  it("deletes the album after confirmation and navigates to the list", async () => {
    setAuthenticated("user-1");
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);

    const router = await mountAt("/albums/album-1/edit");

    const deleteButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    expect(deleteButton).toBeDefined();
    await deleteButton?.click();
    await flushPromises();

    expect(confirm.open).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("Meadowland"),
      }),
    );
    expect(albumsApi.deleteAlbum).toHaveBeenCalledWith("album-1");
    expect(router.currentRoute.value.path).toBe("/albums");
  });

  it("shows an error banner with a retry button", async () => {
    setAuthenticated("user-1");
    vi.mocked(albumsApi.getAlbum).mockRejectedValue(new Error("not found"));

    const router = await mountAt("/albums/album-1/edit");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(albumsApi.getAlbum).mockResolvedValue(
      createAlbum("album-1", "Meadowland"),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    const titleInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    expect(titleInput.value).toBe("Meadowland");
    expect(router.currentRoute.value.path).toBe("/albums/album-1/edit");
  });

  it("surfaces save errors inline", async () => {
    setAuthenticated("user-1");
    vi.mocked(albumsApi.updateAlbum).mockRejectedValue(
      new Error("update failed"),
    );

    await mountAt("/albums/album-1/edit");

    const titleInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    titleInput.value = "Meadowland Updated";
    titleInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    await saveButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("update failed");
  });
});
