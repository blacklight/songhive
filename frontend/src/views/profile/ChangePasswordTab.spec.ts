import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as usersApi from "@/api/users";
import ChangePasswordTab from "./ChangePasswordTab.vue";

vi.mock("@/api/users", () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
  changePassword: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/profile", component: { template: "<div/>" } },
      { path: "/login", component: { template: "<div/>" } },
    ],
  });
}

describe("ChangePasswordTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(usersApi.changePassword).mockResolvedValue({ success: true });

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
      links: [],
    };
    store.status = "authenticated";
  });

  it("submits the change password form", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ChangePasswordTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const inputs = wrapper.findAll('input[type="password"]');
    await inputs[0].setValue("old-password");
    await inputs[1].setValue("new-password");
    await inputs[2].setValue("new-password");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(usersApi.changePassword).toHaveBeenCalledWith({
      current_password: "old-password",
      new_password: "new-password",
    });
  });

  it("rejects non-matching new passwords", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ChangePasswordTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const inputs = wrapper.findAll('input[type="password"]');
    await inputs[0].setValue("old-password");
    await inputs[1].setValue("new-password");
    await inputs[2].setValue("different-password");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(usersApi.changePassword).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(i18n.global.t("profile.passwordMismatch"));
  });
});
