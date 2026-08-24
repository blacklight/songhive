import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import { ApiError } from "@/api/client";
import * as authApi from "@/api/auth";
import PasswordResetConfirmView from "./PasswordResetConfirmView.vue";

vi.mock("@/api/auth", () => ({
  passwordResetRequest: vi.fn(),
  passwordResetConfirm: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/login", component: { template: "<div/>" } },
      {
        path: "/password-reset/confirm",
        component: PasswordResetConfirmView,
      },
    ],
  });
}

describe("PasswordResetConfirmView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows the invalid-token notice when no token is provided", async () => {
    const router = createTestRouter();
    await router.push("/password-reset/confirm");
    await router.isReady();

    const wrapper = mount(PasswordResetConfirmView, {
      global: { plugins: [router] },
    });

    await flushPromises();

    expect(wrapper.text()).toContain(
      i18n.global.t("auth.passwordReset.invalidToken"),
    );
    expect(wrapper.find('button[type="submit"]').attributes("disabled")).toBe(
      "",
    );
    expect(authApi.passwordResetConfirm).not.toHaveBeenCalled();
  });

  it("blocks submission when the passwords do not match", async () => {
    const router = createTestRouter();
    await router.push("/password-reset/confirm?token=reset-123");
    await router.isReady();

    const wrapper = mount(PasswordResetConfirmView, {
      global: { plugins: [router] },
    });

    await wrapper.findAll('input[type="password"]')[0].setValue("new-secret");
    await wrapper.findAll('input[type="password"]')[1].setValue("other");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.passwordResetConfirm).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("auth.passwordReset.passwordMismatch"),
    );
  });

  it("submits a matched token and password, then redirects to login", async () => {
    vi.mocked(authApi.passwordResetConfirm).mockResolvedValue({
      success: true,
    });

    const router = createTestRouter();
    await router.push("/password-reset/confirm?token=reset-123");
    await router.isReady();

    const wrapper = mount(PasswordResetConfirmView, {
      global: { plugins: [router] },
    });

    await wrapper.findAll('input[type="password"]')[0].setValue("new-secret");
    await wrapper.findAll('input[type="password"]')[1].setValue("new-secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.passwordResetConfirm).toHaveBeenCalledWith({
      token: "reset-123",
      new_password: "new-secret",
    });
    expect(router.currentRoute.value.path).toBe("/login");

    const toastStore = useToastStore();
    expect(toastStore.toasts[0]?.message).toBe(
      i18n.global.t("auth.passwordReset.confirmSuccess"),
    );
  });

  it("shows the invalid-token notice on a 400", async () => {
    vi.mocked(authApi.passwordResetConfirm).mockRejectedValue(
      new ApiError("Bad request", 400, { detail: "Token expired" }),
    );

    const router = createTestRouter();
    await router.push("/password-reset/confirm?token=bad");
    await router.isReady();

    const wrapper = mount(PasswordResetConfirmView, {
      global: { plugins: [router] },
    });

    await wrapper.findAll('input[type="password"]')[0].setValue("new-secret");
    await wrapper.findAll('input[type="password"]')[1].setValue("new-secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("Token expired");
    expect(router.currentRoute.value.path).toBe("/password-reset/confirm");
  });
});
