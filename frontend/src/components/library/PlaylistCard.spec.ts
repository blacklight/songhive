import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import PlaylistCard from "./PlaylistCard.vue";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      {
        path: "/playlists/:id",
        name: "playlist",
        component: { template: "<div/>" },
      },
    ],
  });
}

function createPlaylist() {
  return {
    id: "playlist-1",
    name: "Road Trip",
    description: "A mix for the highway.",
    visibility: "public" as const,
    owner_id: "user-1",
  };
}

describe("PlaylistCard", () => {
  let router: ReturnType<typeof createTestRouter>;

  beforeEach(() => {
    setActivePinia(createPinia());
    router = createTestRouter();
  });

  it("renders the playlist metadata and links to the playlist page", async () => {
    const wrapper = mount(PlaylistCard, {
      props: { playlist: createPlaylist() },
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Road Trip");
    expect(wrapper.text()).toContain("A mix for the highway.");
    expect(wrapper.text()).toContain(i18n.global.t("browse.visibility.public"));

    await wrapper.find("a").trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/playlists/playlist-1");
  });

  it("emits click when activated", async () => {
    const wrapper = mount(PlaylistCard, {
      props: { playlist: createPlaylist() },
      global: { plugins: [router] },
    });
    await flushPromises();

    await wrapper.find("a").trigger("click");
    await flushPromises();

    expect(wrapper.emitted("click")?.[0]).toEqual([createPlaylist()]);
  });

  it("shows the current user's name when they own the playlist", async () => {
    const authStore = useAuthStore();
    authStore.user = {
      id: "user-1",
      username: "alice",
      display_name: "Alice",
    } as never;

    const wrapper = mount(PlaylistCard, {
      props: { playlist: createPlaylist() },
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Alice");
    expect(wrapper.text()).not.toContain("user-1");
  });
});
