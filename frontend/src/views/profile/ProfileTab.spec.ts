import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import * as usersApi from "@/api/users";
import ProfileTab from "./ProfileTab.vue";

vi.mock("@/api/users", () => ({
  getMe: vi.fn(),
  updateMe: vi.fn(),
}));

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/profile", component: { template: "<div/>" } }],
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
      links: [{ name: "Home", url: "https://example.com" }],
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

  it("renders the gated actions as disabled", async () => {
    const router = createTestRouter();
    await router.push("/profile");
    await router.isReady();

    const wrapper = mount(ProfileTab, {
      global: { plugins: [router] },
    });
    await flushPromises();

    const submitButtons = wrapper.findAll(
      'button[type="submit"], button[type="button"]',
    );
    const disabledButtons = submitButtons.filter(
      (b) => b.attributes("disabled") === "",
    );
    const labels = disabledButtons.map((b) => b.text());

    expect(labels).toContain(i18n.global.t("profile.passwordChange"));
    expect(labels).toContain(i18n.global.t("profile.resendVerification"));
    expect(labels).toContain(i18n.global.t("profile.deleteAccount"));
  });
});
