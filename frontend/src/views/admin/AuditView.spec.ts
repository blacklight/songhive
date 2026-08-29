import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import * as adminApi from "@/api/admin";
import type { AuditLogResponse } from "@/api/admin";
import AuditView from "./AuditView.vue";

vi.mock("@/api/admin", () => ({
  listAuditLogs: vi.fn(),
}));

function createAuditLog(
  id: string,
  overrides: Partial<AuditLogResponse> = {},
): AuditLogResponse {
  return {
    id,
    action: "user.update",
    actor_id: "admin-1",
    actor_name: "Admin User",
    actor_username: "admin",
    target_type: "user",
    target_id: "user-1",
    target_name: "Alice",
    target_username: "alice",
    details: { role: "admin" },
    ip_address: "127.0.0.1",
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

function createTrackAuditLog(id: string): AuditLogResponse {
  return createAuditLog(id, {
    action: "track.update",
    target_type: "track",
    target_id: "track-1",
    target_name: "My Track",
    target_username: null,
    details: { title: "My Track" },
  });
}

describe("AuditView", () => {
  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(adminApi.listAuditLogs).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("lists audit logs on mount", async () => {
    vi.mocked(adminApi.listAuditLogs).mockResolvedValue([createAuditLog("a1")]);

    wrapper = mount(AuditView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.listAuditLogs).toHaveBeenCalledWith({
      limit: 25,
      offset: 0,
    });
    expect(wrapper.text()).toContain("user.update");
    expect(wrapper.text()).toContain("Admin User");
    expect(wrapper.text()).toContain("Alice");
  });

  it("filters by action and target type", async () => {
    vi.mocked(adminApi.listAuditLogs)
      .mockResolvedValueOnce([createAuditLog("a1")])
      .mockResolvedValueOnce([]);

    wrapper = mount(AuditView, { global: { plugins: [i18n] } });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("user.update");
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(adminApi.listAuditLogs).toHaveBeenLastCalledWith({
      action: "user.update",
      target_type: undefined,
      limit: 25,
      offset: 0,
    });
  });

  it("opens a modal with pretty-printed details", async () => {
    vi.mocked(adminApi.listAuditLogs).mockResolvedValue([createAuditLog("a1")]);

    wrapper = mount(AuditView, {
      attachTo: document.body,
      global: { plugins: [i18n] },
    });
    await flushPromises();

    const detailsButton = wrapper
      .findAll("button")
      .find((b) => b.text() === i18n.global.t("pages.admin.audit.details"));
    await detailsButton?.trigger("click");
    await flushPromises();

    expect(document.body.textContent).toContain("admin");
    expect(document.body.textContent).toContain("role");
  });

  it("renders router links to content targets", async () => {
    vi.mocked(adminApi.listAuditLogs).mockResolvedValue([
      createTrackAuditLog("t1"),
    ]);

    wrapper = mount(AuditView, {
      global: {
        plugins: [i18n],
        stubs: { RouterLink: true },
      },
    });
    await flushPromises();

    const link = wrapper.findComponent({ name: "RouterLink" });
    expect(link.exists()).toBe(true);
    expect(link.props("to")).toEqual({
      name: "track",
      params: { id: "track-1" },
    });
  });
});
