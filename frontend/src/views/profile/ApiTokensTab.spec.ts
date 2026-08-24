import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import { useConfirmStore } from "@/stores/confirm";
import * as authApi from "@/api/auth";
import ApiTokensTab from "./ApiTokensTab.vue";

vi.mock("@/api/auth", () => ({
  listApiTokens: vi.fn(),
  createApiToken: vi.fn(),
  revokeApiToken: vi.fn(),
}));

describe("ApiTokensTab", () => {
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    writeText = vi.fn();

    Object.defineProperty(globalThis, "navigator", {
      value: {
        ...globalThis.navigator,
        clipboard: { writeText },
      },
      writable: true,
      configurable: true,
    });

    vi.mocked(authApi.listApiTokens).mockResolvedValue({
      items: [
        {
          id: "t1",
          name: "Mobile",
          is_active: true,
          created_at: "2026-08-24T10:00:00Z",
          expires_at: null,
          last_used_at: "2026-08-24T11:00:00Z",
        },
      ],
      total: 1,
    });
  });

  it("lists tokens with date columns", async () => {
    mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    expect(authApi.listApiTokens).toHaveBeenCalled();
  });

  it("shows the empty state when no tokens exist", async () => {
    vi.mocked(authApi.listApiTokens).mockResolvedValue({
      items: [],
      total: 0,
    });

    const wrapper = mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("profile.apiTokens.empty"));
  });

  it("displays the raw token after creation and copies it", async () => {
    vi.mocked(authApi.createApiToken).mockResolvedValue({
      id: "t2",
      name: "CI/CD",
      token: "raw-jwt-token",
      expires_at: null,
      created_at: "2026-08-24T12:00:00Z",
    });

    const wrapper = mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    await wrapper.find('input[type="text"]').setValue("CI/CD");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.createApiToken).toHaveBeenCalledWith({ name: "CI/CD" });
    const rawInput = wrapper.find("#raw-token");
    expect((rawInput.element as HTMLInputElement).value).toBe("raw-jwt-token");

    const label = wrapper.find('label[for="raw-token"]');
    expect(label.text()).toBe(i18n.global.t("profile.apiTokens.rawTokenLabel"));

    const copyButton = wrapper
      .findAll('button[type="button"]')
      .find((b) => b.text() === i18n.global.t("common.copy"));
    await copyButton?.trigger("click");
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith("raw-jwt-token");
    const toastStore = useToastStore();
    expect(toastStore.toasts[0]?.message).toBe(
      i18n.global.t("profile.apiTokens.tokenCopied"),
    );
  });

  it("shows an error toast when copying the token fails", async () => {
    vi.mocked(authApi.createApiToken).mockResolvedValue({
      id: "t2",
      name: "CI/CD",
      token: "raw-jwt-token",
      expires_at: null,
      created_at: "2026-08-24T12:00:00Z",
    });

    const wrapper = mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    await wrapper.find('input[type="text"]').setValue("CI/CD");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    writeText.mockRejectedValue(new Error("denied"));

    const copyButton = wrapper
      .findAll('button[type="button"]')
      .find((b) => b.text() === i18n.global.t("common.copy"));
    await copyButton?.trigger("click");
    await flushPromises();

    const toastStore = useToastStore();
    const errorToast = toastStore.toasts.find(
      (t) => t.message === i18n.global.t("profile.apiTokens.copyFailed"),
    );
    expect(errorToast).toBeDefined();
    expect(errorToast?.type).toBe("error");
  });

  it("sends expires_at as an ISO string when provided", async () => {
    vi.mocked(authApi.createApiToken).mockResolvedValue({
      id: "t2",
      name: "CI/CD",
      token: "raw-jwt-token",
      expires_at: null,
      created_at: "2026-08-24T12:00:00Z",
    });

    const wrapper = mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    await wrapper.find('input[type="text"]').setValue("CI/CD");
    await wrapper
      .find('input[type="datetime-local"]')
      .setValue("2026-12-31T23:59");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    const call = vi.mocked(authApi.createApiToken).mock.calls[0][0];
    expect(call.name).toBe("CI/CD");
    expect(call.expires_at).toMatch(
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/,
    );
  });

  it("rejects an expiry date in the past", async () => {
    const wrapper = mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    await wrapper.find('input[type="text"]').setValue("CI/CD");
    await wrapper
      .find('input[type="datetime-local"]')
      .setValue("2020-01-01T00:00");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.createApiToken).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("profile.apiTokens.expiresAtInvalid"),
    );
  });

  it("revokes a token after confirmation and refreshes the list", async () => {
    vi.mocked(authApi.revokeApiToken).mockResolvedValue({ success: true });

    const wrapper = mount(ApiTokensTab, { global: { plugins: [] } });
    await flushPromises();

    const revokeButton = wrapper
      .findAll('button[type="button"]')
      .find((b) => b.text() === i18n.global.t("profile.apiTokens.revoke"));
    const promise = revokeButton?.trigger("click");
    await flushPromises();

    const confirmStore = useConfirmStore();
    expect(confirmStore.state?.message).toBe(
      i18n.global.t("profile.apiTokens.revokeConfirm", { name: "Mobile" }),
    );

    confirmStore.confirm();
    await promise;
    await flushPromises();

    expect(authApi.revokeApiToken).toHaveBeenCalledWith("t1");
    expect(authApi.listApiTokens).toHaveBeenCalledTimes(2);
  });
});
