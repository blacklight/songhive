import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import * as playlistsApi from "@/api/playlists";
import type { PlaylistResponse } from "@/api/playlists";
import PlaylistView from "./PlaylistView.vue";

vi.mock("@/api/playlists", () => ({
  getPlaylist: vi.fn(),
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
    description: "A mix for the highway.",
    visibility: "public",
  };
}

describe("PlaylistView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue(
      createPlaylist("playlist-1", "Road Trip"),
    );
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  async function mountAt(path: string) {
    const router = createTestRouter();
    await router.push(path);
    await router.isReady();
    wrapper = mount(PlaylistView, {
      global: { plugins: [router] },
    });
    await flushPromises();
  }

  it("loads playlist metadata on mount", async () => {
    await mountAt("/playlists/playlist-1");

    expect(playlistsApi.getPlaylist).toHaveBeenCalledWith("playlist-1");

    expect(wrapper.text()).toContain("Road Trip");
    expect(wrapper.text()).toContain("A mix for the highway.");
    expect(wrapper.text()).toContain("Public");
  });

  it("shows the track-listing placeholder", async () => {
    await mountAt("/playlists/playlist-1");

    expect(wrapper.text()).toContain(
      "Track listing is not available for playlists yet.",
    );
  });

  it("shows an error banner with a retry button", async () => {
    vi.mocked(playlistsApi.getPlaylist).mockRejectedValue(
      new Error("not found"),
    );

    await mountAt("/playlists/playlist-1");

    expect(wrapper.text()).toContain("not found");

    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue(
      createPlaylist("playlist-1", "Road Trip"),
    );
    await wrapper.find("button").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Road Trip");
    expect(wrapper.text()).not.toContain("not found");
  });

  it("reloads on route param change", async () => {
    const router = createTestRouter();
    await router.push("/playlists/playlist-1");
    await router.isReady();
    wrapper = mount(PlaylistView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    vi.mocked(playlistsApi.getPlaylist).mockResolvedValue(
      createPlaylist("playlist-2", "Chill"),
    );
    await router.push("/playlists/playlist-2");
    await flushPromises();

    expect(playlistsApi.getPlaylist).toHaveBeenLastCalledWith("playlist-2");
    expect(wrapper.text()).toContain("Chill");
  });
});
