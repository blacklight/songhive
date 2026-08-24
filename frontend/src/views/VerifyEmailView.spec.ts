import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import * as authApi from "@/api/auth";
import VerifyEmailView from "./VerifyEmailView.vue";

vi.mock("@/api/auth", () => ({
  verifyEmail: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", component: { template: "<div/>" } },
      { path: "/verify-email", component: VerifyEmailView },
    ],
  });
}

describe("VerifyEmailView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("shows the invalid-token notice and does not call the API when no token is present", async () => {
    const router = createTestRouter();
    await router.push("/verify-email");
    await router.isReady();

    const wrapper = mount(VerifyEmailView, {
      global: { plugins: [router] },
    });
    await flushPromises();

    expect(authApi.verifyEmail).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("auth.verifyEmail.invalidToken"),
    );
  });

  it("transitions from verifying to success", async () => {
    vi.mocked(authApi.verifyEmail).mockResolvedValue({ success: true });

    const router = createTestRouter();
    await router.push("/verify-email?token=valid");
    await router.isReady();

    const wrapper = mount(VerifyEmailView, {
      global: { plugins: [router] },
    });

    expect(wrapper.text()).toContain(
      i18n.global.t("auth.verifyEmail.verifying"),
    );

    await flushPromises();

    expect(authApi.verifyEmail).toHaveBeenCalledWith({ token: "valid" });
    expect(wrapper.text()).toContain(
      i18n.global.t("auth.verifyEmail.success"),
    );
  });

  it("transitions from verifying to an invalid-token error", async () => {
    vi.mocked(authApi.verifyEmail).mockRejectedValue(new Error("bad token"));

    const router = createTestRouter();
    await router.push("/verify-email?token=invalid");
    await router.isReady();

    const wrapper = mount(VerifyEmailView, {
      global: { plugins: [router] },
    });

    expect(wrapper.text()).toContain(
      i18n.global.t("auth.verifyEmail.verifying"),
    );

    await flushPromises();

    expect(authApi.verifyEmail).toHaveBeenCalledWith({ token: "invalid" });
    expect(wrapper.text()).toContain(
      i18n.global.t("auth.verifyEmail.invalidToken"),
    );
  });
});
