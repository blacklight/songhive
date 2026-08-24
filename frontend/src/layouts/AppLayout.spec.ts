import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises, RouterLinkStub } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import AppLayout from "./AppLayout.vue";
import { useAuthStore } from "@/stores/auth";
import type { UserResponse } from "@/api/users";

async function mountLayout() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return {
    wrapper: mount(AppLayout, {
      global: {
        plugins: [pinia],
        stubs: {
          RouterLink: RouterLinkStub,
          RouterView: true,
        },
      },
    }),
    store: useAuthStore(),
  };
}

describe("AppLayout", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("hides logged-in-only nav items and shows a Login button for anonymous users", async () => {
    const { wrapper } = await mountLayout();
    await flushPromises();

    const links = wrapper.findAll(".app-layout__nav li a");
    const labels = links.map((a) => a.text().trim());

    expect(labels).toEqual([
      "Home",
      "Library",
      "Artists",
      "Albums",
      "Tracks",
      "Playlists",
      "About",
    ]);
    expect(wrapper.find(".app-layout__login").exists()).toBe(true);
    expect(wrapper.find(".app-layout__login").text()).toBe("Log in");
  });

  it("shows logged-in nav items and hides the Login button for authenticated users", async () => {
    const { wrapper, store } = await mountLayout();
    store.accessToken = "token";
    store.refreshToken = "refresh";
    store.expiresAt = Date.now() + 10000;
    store.user = {
      id: "u1",
      username: "alice",
      display_name: null,
      bio: null,
      avatar_url: null,
      links: [],
    } as UserResponse;
    store.role = null;
    store.status = "authenticated";

    await flushPromises();

    const links = wrapper.findAll(".app-layout__nav li a");
    const labels = links.map((a) => a.text().trim());

    expect(labels).toEqual([
      "Home",
      "Library",
      "Artists",
      "Albums",
      "Tracks",
      "Playlists",
      "History",
      "Favorites",
      "Files",
      "Radio",
      "About",
      "Profile",
    ]);
    expect(wrapper.find(".app-layout__login").exists()).toBe(false);
  });

  it("shows the Admin link for logged-in admins", async () => {
    const { wrapper, store } = await mountLayout();
    store.accessToken = "token";
    store.refreshToken = "refresh";
    store.expiresAt = Date.now() + 10000;
    store.user = { id: "u1", username: "admin" } as UserResponse;
    store.role = "admin";
    store.status = "authenticated";

    await flushPromises();

    const adminLink = wrapper.find(".app-layout__admin a");
    expect(adminLink.exists()).toBe(true);
    expect(adminLink.text()).toBe("Admin");
  });
});
