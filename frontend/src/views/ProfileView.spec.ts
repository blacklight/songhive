import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as authApi from "@/api/auth";
import * as usersApi from "@/api/users";
import ProfileView from "./ProfileView.vue";

vi.mock("@/api/auth", () => ({
  listApiTokens: vi.fn(),
  createApiToken: vi.fn(),
  revokeApiToken: vi.fn(),
}));

vi.mock("@/api/users", () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/profile", component: ProfileView },
    ],
  });
}

describe("ProfileView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(authApi.listApiTokens).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(usersApi.updateMe).mockResolvedValue({
      id: "u1",
      username: "alice",
      display_name: null,
      bio: null,
      avatar_url: null,
      links: [],
    });

    const store = useAuthStore();
    store.registerClientProviders();
    store.accessToken = "token";
    store.refreshToken = "refresh";
    store.expiresAt = Date.now() + 10000;
    store.user = {
      id: "u1",
      username: "alice",
      display_name: "Alice",
      bio: "Hello",
      avatar_url: null,
      links: [{ name: "Home", url: "https://example.com" }],
    };
    store.status = "authenticated";
  });

  it("renders the profile tab by default", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(router.currentRoute.value.query.tab).toBeUndefined();
    expect(wrapper.text()).toContain(i18n.global.t("profile.tabs.profile"));
    expect(
      (wrapper.find('input[type="text"]').element as HTMLInputElement).value,
    ).toBe("Alice");
  });

  it("switches to the API tokens tab via query", async () => {
    const router = createTestRouter();
    await router.push("/profile?tab=apiTokens");
    await router.isReady();

    const wrapper = mount(ProfileView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(router.currentRoute.value.query.tab).toBe("apiTokens");
    expect(wrapper.text()).toContain(i18n.global.t("profile.tabs.apiTokens"));
    expect(authApi.listApiTokens).toHaveBeenCalled();
  });

  it("switches to the sessions tab and shows the disabled notice", async () => {
    const router = createTestRouter();
    await router.push("/profile?tab=sessions");
    await router.isReady();

    const wrapper = mount(ProfileView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(router.currentRoute.value.query.tab).toBe("sessions");
    expect(wrapper.text()).toContain(
      i18n.global.t("profile.sessions.disabled"),
    );
  });

  it("updates the query when a tab link is clicked", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const tabLink = wrapper
      .findAll(".profile-view__tab")
      .find((a) => a.text() === i18n.global.t("profile.tabs.apiTokens"));
    expect(tabLink).toBeDefined();
    await tabLink?.trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.query.tab).toBe("apiTokens");
  });
});
