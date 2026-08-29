import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { i18n } from "@/i18n";
import { useToastStore } from "@/stores/toast";
import * as adminApi from "@/api/admin";
import type { AdminUserResponse } from "@/api/admin";
import UsersView from "./UsersView.vue";

vi.mock("@/api/admin", () => ({
  listUsers: vi.fn(),
  promoteUser: vi.fn(),
  demoteUser: vi.fn(),
  activateUser: vi.fn(),
  deactivateUser: vi.fn(),
  deleteUser: vi.fn(),
  bulkUserAction: vi.fn(),
}));

vi.mock("@/composables/useConfirm", () => ({
  useConfirm: vi.fn(),
}));

import { useConfirm } from "@/composables/useConfirm";

function createUser(
  id: string,
  username: string,
  role: AdminUserResponse["role"] = "user",
  isActive = true,
): AdminUserResponse {
  return {
    id,
    username,
    email: `${username}@example.com`,
    role,
    is_active: isActive,
  };
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll("button").find((b) => b.text().trim() === text);
}

describe("UsersView", () => {
  let wrapper: ReturnType<typeof mount>;
  const confirm = vi.fn();

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.clearAllMocks();
    confirm.mockResolvedValue(true);
    vi.mocked(useConfirm).mockReturnValue({ confirm, store: {} as never });
    vi.mocked(adminApi.listUsers).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    wrapper?.unmount();
  });

  it("lists users on mount", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
    ]);

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    expect(adminApi.listUsers).toHaveBeenCalledWith({
      q: "",
      limit: 25,
      offset: 0,
    });
    expect(wrapper.text()).toContain("alice");
  });

  it("searches users with a debounced query", async () => {
    vi.mocked(adminApi.listUsers)
      .mockResolvedValueOnce([createUser("u1", "alice")])
      .mockResolvedValueOnce([createUser("u2", "bob")]);

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const input = wrapper.find('input[type="search"]');
    await input.setValue("bob");
    vi.advanceTimersByTime(300);
    await flushPromises();

    expect(adminApi.listUsers).toHaveBeenLastCalledWith({
      q: "bob",
      limit: 25,
      offset: 0,
    });
    expect(wrapper.text()).toContain("bob");
  });

  it("promotes a user without confirmation", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
    ]);
    vi.mocked(adminApi.promoteUser).mockResolvedValue(
      createUser("u1", "alice", "admin"),
    );

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const promoteButton = findButtonByText(
      wrapper,
      i18n.global.t("pages.admin.users.promote"),
    );
    expect(promoteButton).toBeDefined();
    await promoteButton?.trigger("click");
    await flushPromises();

    expect(confirm).not.toHaveBeenCalled();
    expect(adminApi.promoteUser).toHaveBeenCalledWith("u1");
  });

  it("demotes a user after confirmation", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice", "admin"),
    ]);
    vi.mocked(adminApi.demoteUser).mockResolvedValue(
      createUser("u1", "alice", "user"),
    );

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const demoteButton = findButtonByText(
      wrapper,
      i18n.global.t("pages.admin.users.demote"),
    );
    expect(demoteButton).toBeDefined();
    await demoteButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.demoteUser).toHaveBeenCalledWith("u1");
  });

  it("deletes a user after confirmation", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
    ]);
    vi.mocked(adminApi.deleteUser).mockResolvedValue(undefined);

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const deleteButton = wrapper
      .findAll("button")
      .find(
        (b) =>
          b.attributes("aria-label") ===
          i18n.global.t("pages.admin.users.delete"),
      );
    expect(deleteButton).toBeDefined();
    await deleteButton?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.deleteUser).toHaveBeenCalledWith("u1");
  });

  it("runs a bulk delete after confirmation with recursive true", async () => {
    vi.mocked(adminApi.listUsers).mockResolvedValue([
      createUser("u1", "alice"),
      createUser("u2", "bob"),
    ]);
    vi.mocked(adminApi.bulkUserAction).mockResolvedValue({
      action: "delete",
      processed: 2,
      failed: [],
    });

    wrapper = mount(UsersView, { global: { plugins: [i18n] } });
    await flushPromises();

    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    const selectAll = checkboxes[0];
    await selectAll.setValue(true);
    await flushPromises();

    const bulkDelete = findButtonByText(
      wrapper,
      i18n.global.t("pages.admin.users.bulkDelete"),
    );
    expect(bulkDelete).toBeDefined();
    await bulkDelete?.trigger("click");
    await flushPromises();

    expect(confirm).toHaveBeenCalled();
    expect(adminApi.bulkUserAction).toHaveBeenCalledWith({
      action: "delete",
      user_ids: expect.arrayContaining(["u1", "u2"]),
      recursive: true,
    });
    const toastStore = useToastStore();
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].type).toBe("success");
  });
});
