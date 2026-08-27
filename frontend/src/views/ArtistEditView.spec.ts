import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import * as artistsApi from "@/api/artists";
import type { ArtistResponse, ArtistUpdate } from "@/api/artists";
import ArtistEditView from "./ArtistEditView.vue";

vi.mock("@/api/artists", () => ({
  getArtist: vi.fn(),
  updateArtist: vi.fn(),
  deleteArtist: vi.fn(),
  uploadArtistImage: vi.fn(),
  deleteArtistImage: vi.fn(),
  uploadArtistCover: vi.fn(),
  deleteArtistCover: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/artists/:id", component: { template: "<div/>" } },
      { path: "/artists/:id/edit", component: { template: "<div/>" } },
      { path: "/artists", component: { template: "<div/>" } },
    ],
  });
}

function createArtist(id: string, name: string): ArtistResponse {
  return {
    id,
    name,
    musicbrainz_id: null,
    bio: "A great artist.",
    image_file_id: null,
    image_url: null,
    cover_url: null,
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

describe("ArtistEditView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    vi.mocked(artistsApi.updateArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks Updated"),
    );
    vi.mocked(artistsApi.deleteArtist).mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(ArtistEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
    return router;
  }

  it("loads the artist form for an admin", async () => {
    setAdmin("admin-1");
    const router = await mountAt("/artists/artist-1/edit");

    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(wrapper.text()).toContain("Edit artist");

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    const bioInput = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;
    expect(nameInput.value).toBe("The Larks");
    expect(bioInput.value).toBe("A great artist.");
    expect(router.currentRoute.value.path).toBe("/artists/artist-1/edit");
  });

  it("redirects non-admins to the artist detail page", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/artists/artist-1/edit");

    expect(artistsApi.getArtist).toHaveBeenCalledWith("artist-1");
    expect(router.currentRoute.value.path).toBe("/artists/artist-1");
  });

  it("submits the update form with the correct body", async () => {
    setAdmin("admin-1");
    const router = await mountAt("/artists/artist-1/edit");

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    const bioInput = document.body.querySelector(
      "textarea",
    ) as HTMLTextAreaElement;

    nameInput.value = "The Larks Updated";
    nameInput.dispatchEvent(new Event("input"));
    bioInput.value = "An updated bio.";
    bioInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    const expectedBody: ArtistUpdate = {
      name: "The Larks Updated",
      bio: "An updated bio.",
    };
    expect(artistsApi.updateArtist).toHaveBeenCalledWith(
      "artist-1",
      expectedBody,
    );
    expect(router.currentRoute.value.path).toBe("/artists/artist-1");
  });

  it("deletes the artist after confirmation and navigates to the list", async () => {
    setAdmin("admin-1");
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);

    const router = await mountAt("/artists/artist-1/edit");

    const deleteButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    expect(deleteButton).toBeDefined();
    await deleteButton?.click();
    await flushPromises();

    expect(confirm.open).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("The Larks"),
      }),
    );
    expect(artistsApi.deleteArtist).toHaveBeenCalledWith("artist-1");
    expect(router.currentRoute.value.path).toBe("/artists");
  });

  it("shows an error banner with a retry button", async () => {
    setAdmin("admin-1");
    vi.mocked(artistsApi.getArtist).mockRejectedValue(new Error("not found"));

    const router = await mountAt("/artists/artist-1/edit");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(artistsApi.getArtist).mockResolvedValue(
      createArtist("artist-1", "The Larks"),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    expect(nameInput.value).toBe("The Larks");
    expect(router.currentRoute.value.path).toBe("/artists/artist-1/edit");
  });

  it("surfaces save errors inline", async () => {
    setAdmin("admin-1");
    vi.mocked(artistsApi.updateArtist).mockRejectedValue(
      new Error("update failed"),
    );

    await mountAt("/artists/artist-1/edit");

    const nameInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    nameInput.value = "The Larks Updated";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    await saveButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("update failed");
  });
});
