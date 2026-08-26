import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import * as authApi from "@/api/auth";
import PasswordResetView from "./PasswordResetView.vue";

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
      { path: "/password-reset", component: PasswordResetView },
      {
        path: "/password-reset/confirm",
        component: { template: "<div/>" },
      },
    ],
  });
}

describe("PasswordResetView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("always shows the generic success notice after a request", async () => {
    vi.mocked(authApi.passwordResetRequest).mockResolvedValue({
      success: true,
    });

    const router = createTestRouter();
    await router.push("/password-reset");
    await router.isReady();

    const wrapper = mount(PasswordResetView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.passwordResetRequest).toHaveBeenCalledWith({
      username: "alice",
    });
    expect(wrapper.text()).toContain(
      i18n.global.t("auth.passwordReset.requestSuccess"),
    );
    expect(wrapper.find('input[type="text"]').exists()).toBe(false);
  });
});
