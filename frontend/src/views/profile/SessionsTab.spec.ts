import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import * as authApi from "@/api/auth";
import SessionsTab from "./SessionsTab.vue";

vi.mock("@/api/auth", () => ({
  listSessions: vi.fn(),
  revokeSession: vi.fn(),
  sha256Hex: vi.fn().mockResolvedValue("current-hash"),
}));

describe("SessionsTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();

    const store = useAuthStore();
    store.refreshToken = "my-refresh-token";

    vi.mocked(authApi.listSessions).mockResolvedValue({
      items: [
        {
          id: "current-hash",
          ip_address: "192.0.2.1",
          user_agent: "Mozilla/5.0",
          created_at: "2026-08-31T10:00:00Z",
          expires_at: "2026-09-30T10:00:00Z",
          is_current: true,
        },
        {
          id: "other-hash",
          ip_address: "192.0.2.2",
          user_agent: "SonghiveMobile/1.0",
          created_at: "2026-08-30T10:00:00Z",
          expires_at: "2026-09-29T10:00:00Z",
          is_current: false,
        },
      ],
      total: 2,
    });
  });

  it("lists sessions with the current session marked", async () => {
    const wrapper = mount(SessionsTab, { global: { plugins: [] } });
    await flushPromises();

    expect(authApi.listSessions).toHaveBeenCalledWith("current-hash");
    expect(authApi.sha256Hex).toHaveBeenCalledWith("my-refresh-token");
    expect(wrapper.text()).toContain("Mozilla/5.0");
    expect(wrapper.text()).toContain("SonghiveMobile/1.0");
    expect(wrapper.text()).toContain(i18n.global.t("profile.sessions.current"));
  });

  it("shows the empty state when no sessions exist", async () => {
    vi.mocked(authApi.listSessions).mockResolvedValue({
      items: [],
      total: 0,
    });

    const wrapper = mount(SessionsTab, { global: { plugins: [] } });
    await flushPromises();

    expect(wrapper.text()).toContain(i18n.global.t("profile.sessions.empty"));
  });

  it("revokes a session after confirmation and refreshes the list", async () => {
    vi.mocked(authApi.revokeSession).mockResolvedValue({ success: true });

    const wrapper = mount(SessionsTab, { global: { plugins: [] } });
    await flushPromises();

    const cards = wrapper.findAll(".sessions-tab__card");
    const otherCard = cards.find(
      (card) =>
        !card.text().includes(i18n.global.t("profile.sessions.current")),
    );
    const revokeButton = otherCard
      ?.findAll('button[type="button"]')
      .find((b) => b.text() === i18n.global.t("profile.sessions.revoke"));
    const promise = revokeButton?.trigger("click");
    await flushPromises();

    const confirmStore = useConfirmStore();
    expect(confirmStore.state?.message).toBe(
      i18n.global.t("profile.sessions.revokeConfirm", {
        device: "SonghiveMobile/1.0",
      }),
    );

    confirmStore.confirm();
    await promise;
    await flushPromises();

    expect(authApi.revokeSession).toHaveBeenCalledWith("other-hash");
    expect(authApi.listSessions).toHaveBeenCalledTimes(2);

    const toastStore = useToastStore();
    expect(toastStore.toasts[0]?.message).toBe(
      i18n.global.t("profile.sessions.revokeSuccess"),
    );
  });

  it("revokes the current session and logs the user out", async () => {
    const logout = vi.fn();
    const store = useAuthStore();
    store.logout = logout;

    vi.mocked(authApi.revokeSession).mockResolvedValue({ success: true });

    const wrapper = mount(SessionsTab, { global: { plugins: [] } });
    await flushPromises();

    const cards = wrapper.findAll(".sessions-tab__card");
    const currentCard = cards.find((card) =>
      card.text().includes(i18n.global.t("profile.sessions.current")),
    );
    const revokeButton = currentCard
      ?.findAll('button[type="button"]')
      .find((b) => b.text() === i18n.global.t("profile.sessions.revoke"));
    const promise = revokeButton?.trigger("click");
    await flushPromises();

    const confirmStore = useConfirmStore();
    expect(confirmStore.state?.message).toBe(
      i18n.global.t("profile.sessions.revokeCurrentConfirm"),
    );

    confirmStore.confirm();
    await promise;
    await flushPromises();

    expect(authApi.revokeSession).toHaveBeenCalledWith("current-hash");
    expect(logout).toHaveBeenCalled();
    expect(authApi.listSessions).toHaveBeenCalledTimes(1);
  });
});
