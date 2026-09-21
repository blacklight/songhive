import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useInstanceStore } from "@/stores/instance";
import * as instanceApi from "@/api/instance";
import { ApiError } from "@/api/client";
import LoginView from "./LoginView.vue";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div>home</div>" } },
      { path: "/login", name: "login", component: LoginView },
      {
        path: "/register",
        name: "register",
        component: { template: "<div/>" },
      },
      {
        path: "/password-reset",
        name: "passwordReset",
        component: { template: "<div/>" },
      },
      { path: "/history", name: "history", component: { template: "<div/>" } },
    ],
  });
}

describe("LoginView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("logs in and redirects to a safe redirect target", async () => {
    const router = createTestRouter();
    await router.push("/login?redirect=/history");
    await router.isReady();

    const store = useAuthStore();
    store.login = vi.fn().mockResolvedValue(undefined);
    store.status = "authenticated";

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="password"]').setValue("secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(store.login).toHaveBeenCalledWith("alice", "secret");
    expect(router.currentRoute.value.path).toBe("/history");
  });

  it("rejects protocol-relative and external redirect targets", async () => {
    const router = createTestRouter();
    await router.push("/login?redirect=//evil.com");
    await router.isReady();

    const store = useAuthStore();
    store.login = vi.fn().mockResolvedValue(undefined);
    store.status = "authenticated";

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="password"]').setValue("secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/");
  });

  it("shows the generic failure message on invalid credentials", async () => {
    const router = createTestRouter();
    await router.push("/login");
    await router.isReady();

    const store = useAuthStore();
    store.login = vi.fn().mockRejectedValue(new ApiError("Unauthorized", 401));
    store.status = "error";

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="password"]').setValue("wrong");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("auth.loginPage.failed"));
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("prompts for a second factor when login returns an mfa challenge", async () => {
    const router = createTestRouter();
    await router.push("/login?redirect=/history");
    await router.isReady();

    const store = useAuthStore();
    store.login = vi.fn().mockResolvedValue({
      mfaToken: "mfa-token",
      methods: ["totp", "recovery_code"],
    });
    store.loginWithMfaCode = vi.fn().mockResolvedValue(undefined);
    store.status = "authenticated";

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="password"]').setValue("secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    // Still on the login page, now showing the second-factor form.
    expect(router.currentRoute.value.path).toBe("/login");
    expect(wrapper.text()).toContain(i18n.global.t("auth.loginPage.mfa.title"));

    await wrapper.find('input[type="text"]').setValue("123456");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(store.loginWithMfaCode).toHaveBeenCalledWith("mfa-token", "123456");
    expect(router.currentRoute.value.path).toBe("/history");
  });

  it("returns to the password form when mfa verification fails", async () => {
    const router = createTestRouter();
    await router.push("/login");
    await router.isReady();

    const store = useAuthStore();
    store.login = vi.fn().mockResolvedValue({
      mfaToken: "mfa-token",
      methods: ["totp"],
    });
    store.loginWithMfaCode = vi
      .fn()
      .mockRejectedValue(new ApiError("Unauthorized", 401));
    store.status = "error";

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="password"]').setValue("secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    await wrapper.find('input[type="text"]').setValue("000000");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    // The pending login is single-use: back at the password form with an error.
    expect(wrapper.text()).toContain("Unauthorized");
    expect(wrapper.find('input[type="password"]').exists()).toBe(true);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("shows the email-not-verified message on a 403", async () => {
    const router = createTestRouter();
    await router.push("/login");
    await router.isReady();

    const store = useAuthStore();
    store.login = vi.fn().mockRejectedValue(new ApiError("Forbidden", 403));
    store.status = "error";

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="password"]').setValue("secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("auth.loginPage.emailNotVerified"),
    );
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("redirects to home when already authenticated", async () => {
    const router = createTestRouter();
    await router.push("/login");
    await router.isReady();

    const store = useAuthStore();
    store.status = "authenticated";
    store.user = { id: "u1", username: "alice" } as never;

    mount(LoginView, {
      global: { plugins: [router] },
    });

    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("shows the register link when public registration is open", async () => {
    const router = createTestRouter();
    await router.push("/login");
    await router.isReady();

    const instanceStore = useInstanceStore();
    instanceStore.instance = {
      registrations: true,
    } as unknown as instanceApi.InstanceInfo;

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("auth.loginPage.noAccount"));
  });

  it("hides the register link when public registration is closed", async () => {
    const router = createTestRouter();
    await router.push("/login");
    await router.isReady();

    const instanceStore = useInstanceStore();
    instanceStore.instance = {
      registrations: false,
    } as unknown as instanceApi.InstanceInfo;

    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(wrapper.text()).not.toContain(
      i18n.global.t("auth.loginPage.noAccount"),
    );
  });
});
