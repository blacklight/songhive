import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import * as playlistsApi from "@/api/playlists";
import type {
  PlaylistResponse,
  PlaylistCreate,
  Visibility,
} from "@/api/playlists";
import PlaylistsView from "./PlaylistsView.vue";

vi.mock("@/api/playlists", () => ({
  listPlaylists: vi.fn(),
  createPlaylist: vi.fn(),
  deletePlaylist: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/playlists/:id", component: { template: "<div/>" } },
    ],
  });
}

function createPlaylist(id: string, name: string): PlaylistResponse {
  return {
    id,
    name,
    owner_id: "user-1",
    description: null,
    visibility: "public",
  };
}

function setAuthenticated() {
  const authStore = useAuthStore();
  authStore.accessToken = "token";
  authStore.refreshToken = "refresh";
  authStore.expiresAt = Date.now() + 10000;
  authStore.status = "authenticated";
}

describe("PlaylistsView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(playlistsApi.listPlaylists).mockResolvedValue([]);
    vi.mocked(playlistsApi.createPlaylist).mockResolvedValue(
      createPlaylist("playlist-1", "Road Trip"),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("fetches playlists on mount", async () => {
    vi.mocked(playlistsApi.listPlaylists).mockResolvedValue([
      createPlaylist("playlist-1", "Road Trip"),
    ]);

    wrapper = mount(PlaylistsView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(playlistsApi.listPlaylists).toHaveBeenCalledWith({
      q: "",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Road Trip");
  });

  it("shows the empty state", async () => {
    wrapper = mount(PlaylistsView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("browse.list.empty", {
        entity: i18n.global.t("browse.entities.playlists"),
      }),
    );
  });

  it("debounces search and resets the list", async () => {
    const fetcher = vi.mocked(playlistsApi.listPlaylists);
    fetcher
      .mockResolvedValueOnce([createPlaylist("playlist-1", "First Playlist")])
      .mockResolvedValueOnce([
        createPlaylist("playlist-2", "Searched Playlist"),
      ]);

    wrapper = mount(PlaylistsView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("query");

    vi.advanceTimersByTime(0);
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "query",
      limit: 20,
      offset: 0,
      sort_by: "name",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Searched Playlist");
    expect(wrapper.text()).not.toContain("First Playlist");
  });

  it("loads the next page", async () => {
    const fetcher = vi.mocked(playlistsApi.listPlaylists);
    fetcher
      .mockResolvedValueOnce(
        Array.from({ length: 20 }, (_, i) =>
          createPlaylist(`playlist-${i}`, `Playlist ${i}`),
        ),
      )
      .mockResolvedValueOnce([createPlaylist("playlist-20", "Playlist 20")]);

    wrapper = mount(PlaylistsView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const loadMore = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.loadMore"));
    expect(loadMore).toBeDefined();

    await loadMore?.trigger("click");
    await flushPromises();

    expect(fetcher).toHaveBeenLastCalledWith({
      q: "",
      limit: 20,
      offset: 20,
      sort_by: "name",
      sort_dir: "asc",
    });
    expect(wrapper.text()).toContain("Playlist 19");
    expect(wrapper.text()).toContain("Playlist 20");
  });

  it("creates a playlist and refreshes the list", async () => {
    setAuthenticated();
    const fetcher = vi.mocked(playlistsApi.listPlaylists);
    fetcher
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([createPlaylist("playlist-1", "Road Trip")]);

    wrapper = mount(PlaylistsView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.createPlaylist"));
    expect(createButton).toBeDefined();
    await createButton?.trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#create-playlist-form input[type="text"]',
    ) as HTMLInputElement;
    const descriptionInput = document.body.querySelector(
      "#create-playlist-form textarea",
    ) as HTMLTextAreaElement;
    const visibilityInput = document.body.querySelector(
      "#create-playlist-form select",
    ) as HTMLSelectElement;

    nameInput.value = "Road Trip";
    nameInput.dispatchEvent(new Event("input"));
    descriptionInput.value = "A mix for the highway.";
    descriptionInput.dispatchEvent(new Event("input"));
    visibilityInput.value = "public";
    visibilityInput.dispatchEvent(new Event("change"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    expect(saveButton).toBeDefined();
    await saveButton?.click();
    await flushPromises();

    const expectedBody: PlaylistCreate = {
      name: "Road Trip",
      description: "A mix for the highway.",
    };
    expect(playlistsApi.createPlaylist).toHaveBeenCalledWith(expectedBody, {
      visibility: "public" as Visibility,
    });
    expect(fetcher).toHaveBeenCalledTimes(2);

    const toastStore = useToastStore();
    expect(toastStore.toasts[0].message).toBe(
      i18n.global.t("browse.createPlaylist"),
    );

    expect(document.body.querySelector("#create-playlist-form")).toBeNull();
  });

  it("surfaces creation errors in the modal", async () => {
    setAuthenticated();
    vi.mocked(playlistsApi.createPlaylist).mockRejectedValue(
      new Error("create failed"),
    );

    wrapper = mount(PlaylistsView, {
      attachTo: document.body,
      global: { plugins: [createTestRouter()] },
    });
    await flushPromises();

    const createButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("browse.list.createPlaylist"));
    await createButton?.trigger("click");
    await flushPromises();

    const nameInput = document.body.querySelector(
      '#create-playlist-form input[type="text"]',
    ) as HTMLInputElement;
    nameInput.value = "Road Trip";
    nameInput.dispatchEvent(new Event("input"));
    await flushPromises();

    const saveButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent === i18n.global.t("common.save"));
    await saveButton?.click();
    await flushPromises();

    expect(document.body.textContent).toContain("create failed");
  });
});
