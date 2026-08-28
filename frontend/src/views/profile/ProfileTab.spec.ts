import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as authApi from "@/api/auth";
import * as usersApi from "@/api/users";
import ProfileTab from "./ProfileTab.vue";

vi.mock("@/api/users", () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
  deleteMe: vi.fn(),
}));

vi.mock("@/api/auth", () => ({
  resendVerificationEmail: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div/>" } },
      { path: "/profile", component: { template: "<div/>" } },
    ],
  });
}

describe("ProfileTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(usersApi.updateMe).mockResolvedValue({
      id: "u1",
      username: "alice",
      display_name: "Alice Updated",
      bio: "Updated bio",
      avatar_url: "https://example.com/avatar.png",
      email_verified: true,
      links: [{ name: "Home", url: "https://example.com" }],
    });
    vi.mocked(authApi.resendVerificationEmail).mockResolvedValue({
      success: true,
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
      email_verified: true,
      links: [{ name: "Home", url: "https://example.com" }],
    };
    store.status = "authenticated";
  });

  it("pre-fills from authStore.user", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const textInputs = wrapper.findAll('input[type="text"]');
    expect((textInputs[0].element as HTMLInputElement).value).toBe("Alice");
    expect(
      (wrapper.find("textarea").element as HTMLTextAreaElement).value,
    ).toBe("Hello");
  });

  it("omits blank optional fields and sends the expected payload", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const textInputs = wrapper.findAll('input[type="text"]');
    await textInputs[0].setValue("Alice U.");
    await wrapper.find("textarea").setValue("New bio");
    await wrapper
      .find('input[type="url"]')
      .setValue("https://example.com/avatar.png");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(usersApi.updateMe).toHaveBeenCalledWith({
      display_name: "Alice U.",
      bio: "New bio",
      avatar_url: "https://example.com/avatar.png",
      links: [{ name: "Home", url: "https://example.com" }],
    });
  });

  it("does not show the remove avatar button when there is no avatar", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const removeButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.avatarRemove"));
    expect(removeButton).toBeUndefined();
  });

  it("removes the avatar when the remove button is clicked", async () => {
    const store = useAuthStore();
    store.user = {
      ...store.user!,
      avatar_url: "https://example.com/avatar.png",
    };
    vi.mocked(usersApi.updateMe).mockResolvedValue({
      ...store.user!,
      avatar_url: null,
    });

    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const removeButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.avatarRemove"));
    expect(removeButton).toBeDefined();

    await removeButton!.trigger("click");
    await flushPromises();

    expect(usersApi.updateMe).toHaveBeenCalledWith({ avatar_url: null });
    expect(
      (wrapper.find('input[type="url"]').element as HTMLInputElement).value,
    ).toBe("");
  });

  it("rejects link URLs that do not start with http:// or https://", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const textInputs = wrapper.findAll('input[type="text"]');
    await textInputs[0].setValue("Alice");

    const removeButtons = wrapper.findAll('button[type="button"]');
    const addLinkButton = removeButtons.find(
      (b) => b.text() === i18n.global.t("profile.addLink"),
    );
    await addLinkButton?.trigger("click");
    await flushPromises();

    const rows = wrapper.findAll(".profile-tab__link-row");
    const newRowInputs = rows[rows.length - 1].findAll(
      'input[type="text"], input[type="url"]',
    );
    await newRowInputs[0].setValue("Bad link");
    await newRowInputs[1].setValue("ftp://example.com");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(usersApi.updateMe).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("profile.saveError", {
        message: i18n.global.t("profile.linkUrl"),
      }),
    );
  });

  it("hides resend verification when the user does not need it", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const resendButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.resendVerification"));
    expect(resendButton).toBeUndefined();

    const deleteAccountButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.deleteAccount"));
    expect(deleteAccountButton).toBeDefined();
    expect(deleteAccountButton?.attributes("disabled")).toBeUndefined();
  });

  it("enables resend verification for an unverified user", async () => {
    const store = useAuthStore();
    store.user = { ...store.user!, email_verified: false };

    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const resendButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.resendVerification"));
    expect(resendButton?.attributes("disabled")).toBeUndefined();

    await resendButton?.trigger("click");
    await flushPromises();

    expect(authApi.resendVerificationEmail).toHaveBeenCalledWith({
      username_or_email: "alice",
    });
  });

  it("opens the delete account dialog when the delete button is clicked", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    document.body.innerHTML = "";
    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await flushPromises();

    const deleteAccountButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.deleteAccount"));
    await deleteAccountButton?.trigger("click");
    await flushPromises();

    expect(document.body.querySelector('[role="dialog"]')).toBeTruthy();
    wrapper.unmount();
  });

  it("shows an error when the delete confirmation text does not match", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    document.body.innerHTML = "";
    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await flushPromises();

    const deleteAccountButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.deleteAccount"));
    await deleteAccountButton?.trigger("click");
    await flushPromises();

    const confirmButtons = Array.from(
      document.body.querySelectorAll("button"),
    ).filter(
      (b) =>
        b.textContent?.trim() === i18n.global.t("profile.deleteAccountConfirm"),
    );
    const confirmButton = confirmButtons[confirmButtons.length - 1];
    expect(confirmButton).toBeTruthy();
    confirmButton!.click();
    await flushPromises();

    expect(document.body.textContent).toContain(
      i18n.global.t("profile.deleteAccountConfirmationError"),
    );
    expect(usersApi.deleteMe).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("calls deleteMe and redirects after a confirmed delete", async () => {
    const store = useAuthStore();
    vi.spyOn(store, "logout").mockResolvedValue(undefined);
    vi.mocked(usersApi.deleteMe).mockResolvedValue(undefined);

    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    document.body.innerHTML = "";
    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await flushPromises();

    const deleteAccountButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("profile.deleteAccount"));
    await deleteAccountButton?.trigger("click");
    await flushPromises();

    const textInputs = document.body.querySelectorAll('input[type="text"]');
    const confirmationInput = textInputs[
      textInputs.length - 1
    ] as HTMLInputElement;
    confirmationInput.value = "Yes, I really want to delete my account";
    await confirmationInput.dispatchEvent(
      new Event("input", { bubbles: true }),
    );
    await flushPromises();

    const confirmButtons = Array.from(
      document.body.querySelectorAll("button"),
    ).filter(
      (b) =>
        b.textContent?.trim() === i18n.global.t("profile.deleteAccountConfirm"),
    );
    const confirmButton = confirmButtons[confirmButtons.length - 1];
    confirmButton!.click();
    await flushPromises();

    expect(usersApi.deleteMe).toHaveBeenCalledWith({
      confirmation: "Yes, I really want to delete my account",
      recursive: false,
    });
    expect(store.logout).toHaveBeenCalled();
    expect(router.currentRoute.value.path).toBe("/");
    wrapper.unmount();
  });
});
