import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as authApi from "@/api/auth";
import RegisterView from "./RegisterView.vue";

vi.mock("@/api/auth", () => ({
  register: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/login", component: { template: "<div/>" } },
      { path: "/register", component: RegisterView },
    ],
  });
}

describe("RegisterView", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("blocks submission when passwords do not match", async () => {
    const router = createTestRouter();
    await router.push("/register");
    await router.isReady();

    const wrapper = mount(RegisterView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="email"]').setValue("alice@example.com");
    await wrapper.findAll('input[type="password"]')[0].setValue("secret");
    await wrapper.findAll('input[type="password"]')[1].setValue("other");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.register).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("auth.registerPage.passwordMismatch"),
    );
  });

  it("submits the trimmed payload and navigates to /login on success", async () => {
    const router = createTestRouter();
    await router.push("/register");
    await router.isReady();

    vi.mocked(authApi.register).mockResolvedValue({
      id: "u1",
      username: "alice",
      email: "alice@example.com",
      display_name: null,
      is_active: true,
      email_verified: true,
      role: "user",
    });

    const wrapper = mount(RegisterView, {
      global: { plugins: [router] },
    });

    const textInputs = wrapper.findAll('input[type="text"]');
    await textInputs[0].setValue("alice");
    await wrapper.find('input[type="email"]').setValue("alice@example.com");
    await wrapper.findAll('input[type="password"]')[0].setValue("secret");
    await wrapper.findAll('input[type="password"]')[1].setValue("secret");
    await textInputs[1].setValue("Alice");
    await textInputs[2].setValue("invite-123");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.register).toHaveBeenCalledWith({
      username: "alice",
      email: "alice@example.com",
      password: "secret",
      display_name: "Alice",
      invite_code: "invite-123",
    });
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("prefills the invite code from the URL query string", async () => {
    const router = createTestRouter();
    await router.push("/register?invite_code=pre-filled");
    await router.isReady();

    const wrapper = mount(RegisterView, {
      global: { plugins: [router] },
    });

    const textInputs = wrapper.findAll('input[type="text"]');
    expect(textInputs[2].element.value).toBe("pre-filled");
  });

  it("shows the email verification notice when the response says unverified", async () => {
    const router = createTestRouter();
    await router.push("/register");
    await router.isReady();

    vi.mocked(authApi.register).mockResolvedValue({
      id: "u1",
      username: "alice",
      email: "alice@example.com",
      display_name: null,
      is_active: true,
      email_verified: false,
      role: "user",
    });

    const wrapper = mount(RegisterView, {
      global: { plugins: [router] },
    });

    await wrapper.find('input[type="text"]').setValue("alice");
    await wrapper.find('input[type="email"]').setValue("alice@example.com");
    await wrapper.findAll('input[type="password"]')[0].setValue("secret");
    await wrapper.findAll('input[type="password"]')[1].setValue("secret");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    const toastStore = useToastStore();
    const messages = toastStore.toasts.map((t) => t.message);
    expect(messages).toContain(i18n.global.t("auth.registerPage.success"));
    expect(messages).toContain(
      i18n.global.t("auth.registerPage.emailVerificationNotice"),
    );
    expect(router.currentRoute.value.path).toBe("/login");
  });
});
