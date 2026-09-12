import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import AppLayout from "./AppLayout.vue";
import { useAuthStore } from "@/stores/auth";
import { useNotificationsStore } from "@/stores/notifications";
import type { UserResponse } from "@/api/users";

vi.mock("@/api/ws", () => ({
  eventBus: {
    on: vi.fn(),
    off: vi.fn(),
    connect: vi.fn(),
    disconnect: vi.fn(),
  },
}));

vi.mock("@/api/notifications", () => ({
  listNotifications: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  getUnreadCount: vi.fn().mockResolvedValue(0),
  markSeen: vi.fn(),
  markUnseen: vi.fn(),
  markAllSeen: vi.fn(),
  getNotificationPreferences: vi.fn(),
  updateNotificationPreferences: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/",
        component: { template: "<div/>" },
        children: [
          { path: "", name: "home", component: { template: "<div/>" } },
          {
            path: "search",
            name: "search",
            component: { template: "<div/>" },
          },
          {
            path: "artists",
            name: "artists",
            component: { template: "<div/>" },
          },
          {
            path: "artists/:id",
            name: "artist",
            component: { template: "<div/>" },
          },
          {
            path: "albums",
            name: "albums",
            component: { template: "<div/>" },
          },
          {
            path: "albums/:id",
            name: "album",
            component: { template: "<div/>" },
          },
          {
            path: "playlists",
            name: "playlists",
            component: { template: "<div/>" },
          },
          {
            path: "playlists/:id",
            name: "playlist",
            component: { template: "<div/>" },
          },
          {
            path: "users",
            name: "usersDirectory",
            component: { template: "<div/>" },
          },
          {
            path: "users/:username",
            name: "userRedirect",
            redirect: (to) => ({
              path: `/@${String(to.params.username)}`,
            }),
          },
          {
            path: "@:username",
            component: { template: "<div/>" },
            children: [
              {
                path: "",
                name: "userProfile",
                redirect: { name: "userProfilePosts" },
              },
              {
                path: "posts",
                name: "userProfilePosts",
                component: { template: "<div/>" },
              },
              {
                path: "activity",
                name: "userProfileActivity",
                component: { template: "<div/>" },
              },
            ],
          },
          {
            path: "libraries",
            name: "libraries",
            component: { template: "<div/>" },
          },
          {
            path: "libraries/:id",
            name: "library",
            component: { template: "<div/>" },
          },
          {
            path: "tracks",
            name: "tracks",
            component: { template: "<div/>" },
          },
          {
            path: "tracks/:id",
            name: "track",
            component: { template: "<div/>" },
          },
          {
            path: "files",
            name: "files",
            component: { template: "<div/>" },
          },
          {
            path: "files/:id",
            name: "file",
            component: { template: "<div/>" },
          },
        ],
      },
      { path: "/login", component: { template: "<div/>" } },
      {
        path: "/settings",
        name: "settings",
        component: { template: "<div/>" },
      },
      {
        path: "/@:username",
        name: "userProfile",
        component: { template: "<div/>" },
      },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

function authenticateStore(store: ReturnType<typeof useAuthStore>) {
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
}

async function mountLayout() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createTestRouter();
  return {
    wrapper: mount(AppLayout, {
      global: {
        plugins: [pinia, router],
        stubs: {
          RouterView: true,
        },
      },
    }),
    store: useAuthStore(),
    router,
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
      "Search",
      "Users",
      "Library",
      "Artists",
      "Albums",
      "Tracks",
      "Playlists",
      "Tags",
      "Genres",
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
      "Search",
      "Users",
      "Notifications",
      "Library",
      "Artists",
      "Albums",
      "Tracks",
      "Playlists",
      "Tags",
      "Genres",
      "History",
      "Favorites",
      "Files",
      "Radio",
      "About",
      "Settings",
    ]);
    expect(wrapper.find(".app-layout__login").exists()).toBe(false);
    expect(wrapper.find(".app-layout__user").exists()).toBe(true);
    expect(wrapper.find(".app-layout__user-name").text()).toBe("alice");
    expect(wrapper.find(".app-layout__logout").exists()).toBe(true);
    expect(wrapper.find(".app-layout__logout").attributes("aria-label")).toBe(
      "Log out",
    );
  });

  it("shows the unread badge on the notifications nav item only when > 0", async () => {
    const { wrapper, store } = await mountLayout();
    authenticateStore(store);
    const notifications = useNotificationsStore();
    await flushPromises();

    const link = wrapper
      .findAll(".app-layout__nav li a")
      .find((a) => a.text().includes("Notifications"));
    expect(link).toBeTruthy();
    expect(link!.find(".app-layout__badge").exists()).toBe(false);

    notifications.unreadCount = 5;
    await flushPromises();
    const badge = link!.find(".app-layout__badge");
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe("5");
    expect(badge.attributes("aria-label")).toBe("5 unread notifications");

    notifications.unreadCount = 120;
    await flushPromises();
    expect(link!.find(".app-layout__badge").text()).toBe("99+");
    expect(link!.find(".app-layout__badge").attributes("aria-label")).toBe(
      "120 unread notifications",
    );
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

  it("logs the user out and navigates to /login", async () => {
    const { wrapper, store, router } = await mountLayout();
    store.accessToken = "token";
    store.refreshToken = "refresh";
    store.expiresAt = Date.now() + 10000;
    store.user = {
      id: "u1",
      username: "alice",
      display_name: "Alice",
      bio: null,
      avatar_url: null,
      links: [],
    } as UserResponse;
    store.role = null;
    store.status = "authenticated";

    await flushPromises();

    await wrapper.find(".app-layout__logout").trigger("click");
    await flushPromises();

    expect(store.isAuthenticated).toBe(false);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("highlights the Home nav item only on /", async () => {
    const { wrapper, router } = await mountLayout();
    await router.push("/");
    await flushPromises();

    const home = wrapper
      .findAll(".app-layout__nav li a")
      .find((a) => a.text().trim() === "Home");
    expect(home).toBeTruthy();
    expect(home!.classes()).toContain("router-link-active");
  });

  it("does not highlight the Home nav item when another section is active", async () => {
    const { wrapper, router } = await mountLayout();
    await router.push("/artists");
    await flushPromises();

    const home = wrapper
      .findAll(".app-layout__nav li a")
      .find((a) => a.text().trim() === "Home");
    const artists = wrapper
      .findAll(".app-layout__nav li a")
      .find((a) => a.text().trim() === "Artists");
    expect(home).toBeTruthy();
    expect(artists).toBeTruthy();
    expect(home!.classes()).not.toContain("router-link-active");
    expect(artists!.classes()).toContain("router-link-active");
  });

  it.each([
    { path: "/search", label: "Search" },
    { path: "/albums/abc", label: "Albums" },
    { path: "/artists/abc", label: "Artists" },
    { path: "/playlists/abc", label: "Playlists" },
    { path: "/libraries/abc", label: "Library" },
    { path: "/tracks/abc", label: "Tracks" },
    { path: "/files/abc", label: "Files" },
  ])(
    "highlights the $label nav item while viewing $path",
    async ({ path, label }) => {
      const { wrapper, store, router } = await mountLayout();
      authenticateStore(store);
      await router.push(path);
      await flushPromises();

      const link = wrapper
        .findAll(".app-layout__nav li a")
        .find((a) => a.text().trim() === label);
      const home = wrapper
        .findAll(".app-layout__nav li a")
        .find((a) => a.text().trim() === "Home");
      expect(link).toBeTruthy();
      expect(home).toBeTruthy();
      expect(link!.classes()).toContain("router-link-active");
      expect(home!.classes()).not.toContain("router-link-active");
    },
  );

  it.each([
    { path: "/users", label: "Users" },
    { path: "/users/alice", label: "Users" },
    { path: "/@alice", label: "Users" },
    { path: "/@alice/activity", label: "Users" },
  ])(
    "keeps the Users nav item highlighted while viewing $path",
    async ({ path, label }) => {
      const { wrapper, store, router } = await mountLayout();
      authenticateStore(store);
      await router.push(path);
      await flushPromises();

      const link = wrapper
        .findAll(".app-layout__nav li a")
        .find((a) => a.text().trim() === label);
      const home = wrapper
        .findAll(".app-layout__nav li a")
        .find((a) => a.text().trim() === "Home");
      expect(link).toBeTruthy();
      expect(home).toBeTruthy();
      expect(link!.classes()).toContain("router-link-active");
      expect(home!.classes()).not.toContain("router-link-active");
    },
  );
});
