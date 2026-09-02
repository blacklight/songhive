import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import * as tracksApi from "@/api/tracks";
import type { TrackResponse, TrackUpdate } from "@/api/tracks";
import TrackEditView from "./TrackEditView.vue";

vi.mock("@/api/tracks", () => ({
  getTrack: vi.fn(),
  updateTrack: vi.fn(),
  deleteTrack: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/tracks/:id", component: { template: "<div/>" } },
      { path: "/tracks/:id/edit", component: { template: "<div/>" } },
      { path: "/tracks", component: { template: "<div/>" } },
    ],
  });
}

function createTrack(
  id: string,
  title: string,
  ownerId = "user-1",
): TrackResponse {
  return {
    id,
    title,
    artist_id: "artist-1",
    album_id: "album-1",
    track_number: 1,
    disc_number: 1,
    duration: 185,
    genre: "Rock",
    audio_url: "https://example.com/audio.mp3",
    visibility: "public",
    filename: "Song One.mp3",
    owner_id: ownerId,
    hashtags: [],
    genres: ["rock"],
    artist: { id: "artist-1", name: "Sample Artist" },
    album: {
      id: "album-1",
      title: "Sample Album",
      artist_id: "artist-1",
      visibility: "public",
    },
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

describe("TrackEditView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );
    vi.mocked(tracksApi.updateTrack).mockResolvedValue(
      createTrack("track-1", "Song One Updated"),
    );
    vi.mocked(tracksApi.deleteTrack).mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(TrackEditView, {
      attachTo: document.body,
      global: { plugins: [router] },
    });
    await flushPromises();
    return router;
  }

  it("loads the track form for the owner", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/tracks/track-1/edit");

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album,hashtags,genres",
    });
    expect(wrapper.text()).toContain("Edit track");

    const inputs = document.body.querySelectorAll(
      'input[type="text"], input[type="number"]',
    );
    const titleInput = inputs[0] as HTMLInputElement;
    const artistInput = inputs[1] as HTMLInputElement;
    const albumInput = inputs[2] as HTMLInputElement;
    expect(titleInput.value).toBe("Song One");
    expect(artistInput.value).toBe("Sample Artist");
    expect(albumInput.value).toBe("Sample Album");
    expect(router.currentRoute.value.path).toBe("/tracks/track-1/edit");
  });

  it("redirects non-owners to the track detail page", async () => {
    setAuthenticated("user-2");
    const router = await mountAt("/tracks/track-1/edit");

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album,hashtags,genres",
    });
    expect(router.currentRoute.value.path).toBe("/tracks/track-1");
  });

  it("loads the track form for an admin who is not the owner", async () => {
    setAdmin("admin-1");
    const router = await mountAt("/tracks/track-1/edit");

    expect(tracksApi.getTrack).toHaveBeenCalledWith("track-1", {
      include: "artist,album,hashtags,genres",
    });
    expect(wrapper.text()).toContain("Edit track");
    expect(router.currentRoute.value.path).toBe("/tracks/track-1/edit");
  });

  it("submits the update form with the correct body", async () => {
    setAuthenticated("user-1");
    const router = await mountAt("/tracks/track-1/edit");

    const inputs = document.body.querySelectorAll(
      'input[type="text"], input[type="number"]',
    );
    // Order: title, artist, album, track number, disc number, release year
    const titleInput = inputs[0] as HTMLInputElement;
    const artistInput = inputs[1] as HTMLInputElement;
    const albumInput = inputs[2] as HTMLInputElement;
    const trackNumberInput = inputs[4] as HTMLInputElement;
    const discNumberInput = inputs[5] as HTMLInputElement;
    const releaseYearInput = inputs[6] as HTMLInputElement;
    const visibilityInput = document.body.querySelector(
      "select",
    ) as HTMLSelectElement;

    titleInput.value = "Song One Updated";
    titleInput.dispatchEvent(new Event("input"));
    artistInput.value = "Sample Artist";
    artistInput.dispatchEvent(new Event("input"));
    albumInput.value = "Sample Album";
    albumInput.dispatchEvent(new Event("input"));
    trackNumberInput.value = "2";
    trackNumberInput.dispatchEvent(new Event("input"));
    discNumberInput.value = "";
    discNumberInput.dispatchEvent(new Event("input"));
    releaseYearInput.value = "";
    releaseYearInput.dispatchEvent(new Event("input"));
    visibilityInput.value = "local";
    visibilityInput.dispatchEvent(new Event("change"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    const expectedBody: TrackUpdate = {
      title: "Song One Updated",
      artist_name: "Sample Artist",
      album_title: "Sample Album",
      genre: "rock",
      track_number: 2,
      disc_number: null,
      release_year: null,
      visibility: "local",
      filename: "Song One.mp3",
    };
    expect(tracksApi.updateTrack).toHaveBeenCalledWith("track-1", expectedBody);
    expect(router.currentRoute.value.path).toBe("/tracks/track-1");
  });

  it("deletes the track after confirmation and navigates to the list", async () => {
    setAuthenticated("user-1");
    const confirm = useConfirmStore();
    vi.spyOn(confirm, "open").mockResolvedValue(true);

    const router = await mountAt("/tracks/track-1/edit");

    const deleteButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.delete"));
    expect(deleteButton).toBeDefined();
    await deleteButton?.click();
    await flushPromises();

    expect(confirm.open).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining("Song One"),
      }),
    );
    expect(tracksApi.deleteTrack).toHaveBeenCalledWith("track-1");
    expect(router.currentRoute.value.path).toBe("/tracks");
  });

  it("shows an error banner with a retry button", async () => {
    setAuthenticated("user-1");
    vi.mocked(tracksApi.getTrack).mockRejectedValue(new Error("not found"));

    const router = await mountAt("/tracks/track-1/edit");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(tracksApi.getTrack).mockResolvedValue(
      createTrack("track-1", "Song One"),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    const inputs = document.body.querySelectorAll(
      'input[type="text"], input[type="number"]',
    );
    const titleInput = inputs[0] as HTMLInputElement;
    expect(titleInput.value).toBe("Song One");
    expect(router.currentRoute.value.path).toBe("/tracks/track-1/edit");
  });

  it("surfaces save errors inline", async () => {
    setAuthenticated("user-1");
    vi.mocked(tracksApi.updateTrack).mockRejectedValue(
      new Error("update failed"),
    );

    await mountAt("/tracks/track-1/edit");

    const titleInput = document.body.querySelector(
      'input[type="text"]',
    ) as HTMLInputElement;
    titleInput.value = "Song One Updated";
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
