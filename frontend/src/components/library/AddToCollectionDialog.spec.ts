import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { ApiError } from "@/api/client";
import AddToCollectionDialog from "./AddToCollectionDialog.vue";
import * as librariesApi from "@/api/libraries";
import * as playlistsApi from "@/api/playlists";

vi.mock("@/api/libraries", () => ({
  listLibraries: vi.fn(),
  createLibrary: vi.fn(),
  addTracksToLibrary: vi.fn(),
}));

vi.mock("@/api/playlists", () => ({
  listPlaylists: vi.fn(),
  createPlaylist: vi.fn(),
  addTracksToPlaylist: vi.fn(),
}));

const sampleLibrary: librariesApi.LibraryResponse = {
  id: "lib-1",
  name: "My Library",
  owner_id: "u1",
  can_write: true,
  visibility: "private",
};

const samplePlaylist: playlistsApi.PlaylistResponse = {
  id: "playlist-1",
  name: "My Playlist",
  owner_id: "u1",
  visibility: "private",
};

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div/>" } }],
  });
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(AddToCollectionDialog, {
    props: {
      open: true,
      mode: "library",
      itemType: "track",
      itemId: "track-1",
      itemName: "Song One",
      ...props,
    },
    attachTo: document.body,
    global: { plugins: [createTestRouter()] },
  });
}

function findButton(text: string) {
  return Array.from(document.body.querySelectorAll("button")).find(
    (b) => b.textContent === text,
  );
}

function findByText(text: string) {
  return Array.from(document.body.querySelectorAll("*")).find((el) =>
    el.textContent?.includes(text),
  );
}

describe("AddToCollectionDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    const authStore = useAuthStore();
    authStore.$patch({
      accessToken: "token",
      expiresAt: Date.now() + 60000,
      user: { id: "u1", username: "user" },
    });
    vi.mocked(librariesApi.listLibraries).mockReset();
    vi.mocked(librariesApi.createLibrary).mockReset();
    vi.mocked(librariesApi.addTracksToLibrary).mockReset();
    vi.mocked(playlistsApi.listPlaylists).mockReset();
    vi.mocked(playlistsApi.createPlaylist).mockReset();
    vi.mocked(playlistsApi.addTracksToPlaylist).mockReset();
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders a spinner while collections load", async () => {
    let resolveList: (value: librariesApi.LibraryResponse[]) => void = () => {};
    vi.mocked(librariesApi.listLibraries).mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );

    mountDialog();
    await flushPromises();

    expect(document.body.querySelector(".app-spinner")).not.toBeNull();

    resolveList([sampleLibrary]);
    await flushPromises();

    expect(document.body.querySelector(".app-spinner")).toBeNull();
  });

  it("loads collections and pre-selects the first option", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([sampleLibrary]);

    mountDialog();
    await flushPromises();

    expect(librariesApi.listLibraries).toHaveBeenCalledWith({ limit: 100 });
    const select = document.body.querySelector(
      "select",
    ) as HTMLSelectElement | null;
    expect(select).not.toBeNull();
    expect(select?.value).toBe("lib-1");
  });

  it("calls addTracksToLibrary with the right body for a track", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([sampleLibrary]);
    vi.mocked(librariesApi.addTracksToLibrary).mockResolvedValue({
      added: 1,
      track_ids: ["track-1"],
    });

    mountDialog();
    await flushPromises();

    const saveButton = findButton(i18n.global.t("common.save"));
    saveButton?.click();
    await flushPromises();

    expect(librariesApi.addTracksToLibrary).toHaveBeenCalledWith("lib-1", {
      track_ids: ["track-1"],
    });
  });

  it("calls addTracksToLibrary with album_id for an album", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([sampleLibrary]);
    vi.mocked(librariesApi.addTracksToLibrary).mockResolvedValue({
      added: 1,
      track_ids: [],
    });

    mountDialog({ itemType: "album", itemId: "album-1" });
    await flushPromises();

    const saveButton = findButton(i18n.global.t("common.save"));
    saveButton?.click();
    await flushPromises();

    expect(librariesApi.addTracksToLibrary).toHaveBeenCalledWith("lib-1", {
      album_id: "album-1",
    });
  });

  it("calls addTracksToPlaylist with artist_id for an artist", async () => {
    vi.mocked(playlistsApi.listPlaylists).mockResolvedValue([samplePlaylist]);
    vi.mocked(playlistsApi.addTracksToPlaylist).mockResolvedValue({
      added: 2,
      track_ids: ["t1", "t2"],
    });

    mountDialog({
      mode: "playlist",
      itemType: "artist",
      itemId: "artist-1",
    });
    await flushPromises();

    const saveButton = findButton(i18n.global.t("common.save"));
    saveButton?.click();
    await flushPromises();

    expect(playlistsApi.addTracksToPlaylist).toHaveBeenCalledWith(
      "playlist-1",
      { artist_id: "artist-1" },
    );
  });

  it("creates a new library and adds the item to it", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([]);
    vi.mocked(librariesApi.createLibrary).mockResolvedValue({
      ...sampleLibrary,
      id: "new-lib",
      name: "New Library",
    });
    vi.mocked(librariesApi.addTracksToLibrary).mockResolvedValue({
      added: 1,
      track_ids: ["track-1"],
    });

    mountDialog();
    await flushPromises();

    const newOption = Array.from(document.body.querySelectorAll("option")).find(
      (o) => o.value === "__new__",
    ) as HTMLOptionElement | null;
    expect(newOption).not.toBeNull();

    const select = document.body.querySelector(
      "select",
    ) as HTMLSelectElement | null;
    if (select && newOption) {
      select.value = "__new__";
      select.dispatchEvent(new Event("change"));
    }
    await flushPromises();

    const input = document.body.querySelector(
      "input",
    ) as HTMLInputElement | null;
    expect(input).not.toBeNull();
    if (input) {
      input.value = "New Library";
      input.dispatchEvent(new Event("input"));
    }
    await flushPromises();

    const saveButton = findButton(i18n.global.t("common.save"));
    saveButton?.click();
    await flushPromises();

    expect(librariesApi.createLibrary).toHaveBeenCalledWith(
      { name: "New Library", description: null },
      { visibility: "private" },
    );
    expect(librariesApi.addTracksToLibrary).toHaveBeenCalledWith("new-lib", {
      track_ids: ["track-1"],
    });
  });

  it("displays an inline error when loading collections fails", async () => {
    vi.mocked(librariesApi.listLibraries).mockRejectedValue(
      new Error("network down"),
    );

    mountDialog();
    await flushPromises();

    expect(findByText("network down")).not.toBeUndefined();
  });

  it("displays an inline error when saving fails", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([sampleLibrary]);
    vi.mocked(librariesApi.addTracksToLibrary).mockRejectedValue(
      new Error("save failed"),
    );

    mountDialog();
    await flushPromises();

    const saveButton = findButton(i18n.global.t("common.save"));
    saveButton?.click();
    await flushPromises();

    expect(findByText("save failed")).not.toBeUndefined();
  });

  it("shows an empty message when there are no collections and the user cannot create one", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([]);

    const authStore = useAuthStore();
    authStore.$patch({
      accessToken: null,
      user: null,
    });

    mountDialog();
    await flushPromises();

    expect(
      findByText(i18n.global.t("browse.addToCollection.empty")),
    ).not.toBeUndefined();
  });

  it("emits close when cancel is clicked without calling APIs", async () => {
    vi.mocked(librariesApi.listLibraries).mockResolvedValue([sampleLibrary]);

    const wrapper = mountDialog();
    await flushPromises();

    const cancelButton = findButton(i18n.global.t("common.cancel"));
    cancelButton?.click();
    await flushPromises();

    expect(wrapper.emitted("close")?.length).toBe(1);
    expect(librariesApi.addTracksToLibrary).not.toHaveBeenCalled();
  });

  it("shows a duplicate warning for playlists and resubmits with allow_duplicates", async () => {
    vi.mocked(playlistsApi.listPlaylists).mockResolvedValue([samplePlaylist]);
    vi.mocked(playlistsApi.addTracksToPlaylist)
      .mockRejectedValueOnce(
        new ApiError("Tracks already in playlist", 409, {
          detail: "Tracks already in playlist",
        }),
      )
      .mockResolvedValueOnce({
        added: 1,
        track_ids: ["track-1"],
      });

    mountDialog({ mode: "playlist" });
    await flushPromises();

    const saveButton = findButton(i18n.global.t("common.save"));
    saveButton?.click();
    await flushPromises();

    expect(playlistsApi.addTracksToPlaylist).toHaveBeenCalledWith(
      "playlist-1",
      { track_ids: ["track-1"] },
    );
    expect(
      findByText(i18n.global.t("browse.addToCollection.duplicateWarning")),
    ).not.toBeUndefined();

    const checkbox = document.body.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement | null;
    expect(checkbox).not.toBeNull();
    if (checkbox) {
      checkbox.checked = true;
      checkbox.dispatchEvent(new Event("change"));
    }
    await flushPromises();

    const addAnywayButton = findButton(
      i18n.global.t("browse.addToCollection.addAnyway"),
    );
    addAnywayButton?.click();
    await flushPromises();

    expect(playlistsApi.addTracksToPlaylist).toHaveBeenCalledWith(
      "playlist-1",
      { track_ids: ["track-1"], allow_duplicates: true },
    );
  });
});
